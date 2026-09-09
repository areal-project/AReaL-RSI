"""真实 loopback HTTP 验证只读面板，不调用模型或外部服务。"""

from __future__ import annotations

import fcntl
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any

from areal_rsi.api.base import (
    ContentRef,
    EvolutionTarget,
    ExperimentPlan,
    ExperimentRun,
    HarnessVersion,
    ModelVersion,
    ResearchAgentState,
    RunState,
    SolutionVersion,
)
from areal_rsi.base.storage import write_json
from areal_rsi.ui.web import ResearchServer, RunReader


class WebTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        content = ContentRef("content://fixture", "sha256:fixture")
        baseline = SolutionVersion(
            "solution_base",
            ModelVersion("model_target", "task-agent-fixture-model", content, "fixture-time"),
            HarnessVersion("harness_base", "fixture-harness", content, "fixture-time"),
            "fixture-time",
        )
        write_json(self.root / "versions" / "solution_base.json", baseline)
        plan = ExperimentPlan(
            "真实记录字段 <script>alert(1)</script>",
            "harness-evolution",
            "fixture",
            "solution_base",
            EvolutionTarget.HARNESS,
            3,
            research_agent_count=2,
            config={
                "teacher_model_name": "teacher-default-fixture-model",
                "interaction_model_name": "interaction-agent-fixture-model",
                "agent_models": {
                    "rsi-agent": {"model": "rsi-agent-fixture-model"},
                    "research-agent-1": {"model": "research-agent-fixture-model"},
                    "research-agent-2": {"model": "research-agent-fixture-model"},
                },
                "secret": "must-not-leak",
            },
        )
        self.experiment = ExperimentRun(
            "run_fixture",
            plan,
            RunState.RUNNING,
            "solution_base",
            research_agents=(
                ResearchAgentState("research-agent-1", RunState.RUNNING),
                ResearchAgentState("research-agent-2", RunState.RUNNING),
            ),
        )
        write_json(self.root / "run-states" / "run_fixture.json", self.experiment)
        write_json(
            self.root / "runs" / "run_fixture" / "progress.json",
            {
                "research-agent-1": {"phase": "propose", "iteration": 1},
            },
        )
        self.server = ResearchServer(self.root, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.server.server_close()

    def request(
        self, path: str, *, method: str = "GET", headers: dict[str, str] | None = None
    ) -> tuple[int, Any, bytes]:
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            client.request(method, path, headers=headers or {})
            response = client.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            client.close()

    def test_live_projection_and_no_private_content(self) -> None:
        listing = json.loads(self.request("/api/runs")[2])
        self.assertEqual(listing["workspace_root"], str(self.root.resolve()))
        status, headers, body = self.request("/api/runs/run_fixture")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        data = json.loads(body)
        self.assertEqual(data["teacher_model"], "teacher-default-fixture-model")
        self.assertEqual(data["interaction_model"], "interaction-agent-fixture-model")
        self.assertEqual(data["rsi_model"], "rsi-agent-fixture-model")
        self.assertEqual(
            [agent["model"] for agent in data["agents"]],
            [
                "research-agent-fixture-model",
                "research-agent-fixture-model",
            ],
        )
        self.assertEqual(data["target_model"], "task-agent-fixture-model")
        self.assertEqual(data["model"], "task-agent-fixture-model")
        self.assertFalse(data["worker_alive"])
        self.assertTrue(all("direction" not in agent for agent in data["agents"]))
        self.assertEqual(data["agents"][0]["phase"], "propose")
        self.assertNotIn(b"must-not-leak", body)
        self.assertNotIn("config", data)
        self.experiment.state = RunState.FAILED
        self.experiment.error = "明确的执行失败"
        write_json(self.root / "run-states" / "run_fixture.json", self.experiment)
        updated = json.loads(self.request("/api/runs/run_fixture")[2])
        self.assertEqual(updated["state"], "failed")
        self.assertEqual(updated["error"], "明确的执行失败")

    def test_process_liveness_uses_os_lock_not_old_file(self) -> None:
        lock_path = self.root / "locks" / "run_fixture.lock"
        lock_path.parent.mkdir()
        with lock_path.open("w") as lock:
            self.assertFalse(RunReader(self.root).worker_alive("run_fixture"))
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertTrue(json.loads(self.request("/api/runs/run_fixture")[2])["worker_alive"])
        self.assertFalse(RunReader(self.root).worker_alive("run_fixture"))

    def test_asset_allowlist_and_read_only_routes(self) -> None:
        for path in ("/", "/app.js", "/style.css"):
            status, headers, body = self.request(path)
            self.assertEqual(status, 200, path)
            self.assertTrue(body)
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        for path in (
            "/gateway/process.json",
            "/content/any",
            "/../../etc/passwd",
            "/api/runs/run_missing",
        ):
            self.assertEqual(self.request(path)[0], 404, path)
        self.assertEqual(self.request("/api/runs/%2e%2e%2fsecret")[0], 400)
        self.assertEqual(self.request("/api/runs", method="POST")[0], 405)
        self.assertEqual(self.request("/api/runs", headers={"Host": "external.example"})[0], 403)
        self.assertEqual(len(json.loads(self.request("/api/runs")[2])["runs"]), 1)
        self.assertEqual(RunReader(self.root, "run_other").list(), [])
        with self.assertRaises(FileNotFoundError):
            RunReader(self.root, "run_other").detail("run_fixture")
