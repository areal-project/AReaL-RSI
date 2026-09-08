from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, cast

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent

from areal_rsi.config import ModelConfigurationError
from areal_rsi.ui.experiments import ExperimentController
from areal_rsi.ui.mcp import create_server


class FixtureController(ExperimentController):
    """只用于协议进程的离线替身，不加载 App，也不调用任何模型。"""

    def __init__(self, root: Path):
        self.fixture_root = root
        self.fixture_plans: set[str] = set()
        self.child: subprocess.Popen[bytes] | None = None

    def record(self, operation: str) -> None:
        with (self.fixture_root / "calls.txt").open("a") as stream:
            stream.write(operation + "\n")

    async def plan(self, goal: str, max_iterations: int) -> dict[str, Any]:
        self.record("plan")
        self.fixture_plans.add("plan_fixture")
        return {
            "plan_id": "plan_fixture",
            "confirmation_required": True,
            "plan": {
                "goal": goal,
                "max_iterations": max_iterations,
                "workflow": "harness-evolution",
                "target": "harness",
                "benchmark": "fixed-benchmark",
                "baseline_solution_id": "solution_fixture",
                "research_agent_count": 2,
                "config": {
                    "teacher_model_name": "teacher-default-fixture-model",
                    "target_model_name": "task-agent-fixture-model",
                    "interaction_model_name": "interaction-agent-fixture-model",
                    "agent_models": {
                        "rsi-agent": {"model": "rsi-agent-fixture-model", "api_key": "SECRET"},
                        "research-agent-1": {
                            "model": "research-agent-fixture-model",
                            "base_url": "SECRET",
                        },
                    },
                    "api_key": "SECRET",
                },
            },
            "environment": {"token": "SECRET"},
        }

    async def start(self, plan_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        if plan_id not in self.fixture_plans:
            raise KeyError("SECRET details about a missing plan")
        if confirmed is not True:
            raise ValueError("confirmation required")
        self.record("start")
        if (self.fixture_root / "spawn-worker").exists() and self.child is None:
            self.child = subprocess.Popen(
                [sys.executable, "-c", "import signal; signal.pause()"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                cwd=self.fixture_root,
            )
        return {
            "run_id": "run_fixture",
            "state": "running",
            "worker": {
                "active": True,
                "pid": self.child.pid if self.child else None,
                "status": "running",
                "log_path": "worker.log",
                "token": "SECRET",
            },
        }

    async def status(self, run_id: str | None = None) -> dict[str, Any]:
        self.record("status")
        if run_id == "explode":
            raise RuntimeError("SECRET API credential and private HTTP body")
        if run_id == "configuration":
            raise ModelConfigurationError("该数据目录已绑定其他模型，请使用新的 RSI_DATA_ROOT。")
        if run_id == "oversized":
            return {"worker": {"log_path": "x" * 25_000}}
        result = {
            "run_id": "run_fixture",
            "state": "running",
            "goal": "goal" * 2000,
            "completed_iterations": 1,
            "max_iterations": 3,
            "research_agent_count": 2,
            "trajectories": ["SECRET validation details"] * 1000,
            "research_agents": [{"iterations": [{"expected": "SECRET"}]}],
        }
        return result if run_id else {"runs": [result] * 30}

    async def stop(self, run_id: str) -> dict[str, Any]:
        self.record("stop")
        return {"run_id": run_id, "state": "cancelled", "stop_requested": True}

    async def resume(self, run_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        if confirmed is not True:
            raise ValueError("confirmation required")
        self.record("resume")
        return {"run_id": run_id, "state": "running"}


class RsiMcpTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    @asynccontextmanager
    async def client(self) -> AsyncIterator[ClientSession]:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[str(Path(__file__).resolve()), "--mcp-fixture", str(self.root)],
        )
        with (self.root / "stderr.log").open("w") as errors:
            async with stdio_client(parameters, errlog=errors) as (read, write):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    self.assertEqual(initialized.server_info.name, "areal-rsi")
                    self.assertIsNotNone(initialized.capabilities.tools)
                    yield session

    def body(self, result: CallToolResult) -> dict[str, Any]:
        self.assertFalse(result.is_error)
        self.assertEqual(len(result.content), 1)
        item = result.content[0]
        self.assertIsInstance(item, TextContent)
        assert isinstance(item, TextContent)
        self.assertLessEqual(len(item.text), 24_000)
        self.assertNotIn("SECRET", item.text)
        return cast(dict[str, Any], json.loads(item.text))

    async def test_stdio_handshake_schemas_and_confirmed_lifecycle(self) -> None:
        async with self.client() as session:
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            self.assertEqual(
                set(tools), {"rsi_plan", "rsi_start", "rsi_status", "rsi_stop", "rsi_resume"}
            )
            for name in ("rsi_start", "rsi_resume"):
                self.assertIn("confirmed", tools[name].input_schema["required"])
                self.assertEqual(
                    tools[name].input_schema["properties"]["confirmed"]["type"], "boolean"
                )
            self.assertIn("plan_id", tools["rsi_start"].input_schema["required"])
            status_annotations = tools["rsi_status"].annotations
            start_annotations = tools["rsi_start"].annotations
            stop_annotations = tools["rsi_stop"].annotations
            assert status_annotations is not None
            assert start_annotations is not None
            assert stop_annotations is not None
            self.assertTrue(status_annotations.read_only_hint)
            self.assertFalse(start_annotations.read_only_hint)
            self.assertTrue(stop_annotations.destructive_hint)
            plan = self.body(
                await session.call_tool(
                    "rsi_plan", {"goal": "Improve controls", "max_iterations": 3}
                )
            )
            self.assertEqual(plan["plan"]["benchmark"], "fixed-benchmark")
            self.assertEqual(plan["plan"]["target"], "harness")
            self.assertEqual(
                plan["plan"]["config"]["teacher_model_name"],
                "teacher-default-fixture-model",
            )
            self.assertEqual(
                plan["plan"]["config"]["target_model_name"], "task-agent-fixture-model"
            )
            self.assertEqual(
                plan["plan"]["config"]["interaction_model_name"], "interaction-agent-fixture-model"
            )
            self.assertEqual(
                plan["plan"]["config"]["agent_models"],
                {
                    "rsi-agent": {"model": "rsi-agent-fixture-model"},
                    "research-agent-1": {"model": "research-agent-fixture-model"},
                },
            )
            self.assertTrue(plan["confirmation_required"])
            started = self.body(
                await session.call_tool(
                    "rsi_start", {"plan_id": plan["plan_id"], "confirmed": True}
                )
            )
            self.assertEqual(started["run_id"], "run_fixture")
            status = self.body(await session.call_tool("rsi_status", {"run_id": "run_fixture"}))
            self.assertEqual(status["completed_iterations"], 1)
            stopped = self.body(await session.call_tool("rsi_stop", {"run_id": "run_fixture"}))
            self.assertTrue(stopped["stop_requested"])
            resumed = self.body(
                await session.call_tool("rsi_resume", {"run_id": "run_fixture", "confirmed": True})
            )
            self.assertEqual(resumed["state"], "running")
        self.assertEqual(
            (self.root / "calls.txt").read_text().splitlines(),
            ["plan", "start", "status", "stop", "resume"],
        )

    async def test_confirmation_requires_boolean_true_and_existing_plan(self) -> None:
        async with self.client() as session:
            for confirmed in (False, "true", 1, None):
                with self.subTest(confirmed=confirmed):
                    for name, key in (("rsi_start", "plan_id"), ("rsi_resume", "run_id")):
                        response = await session.call_tool(
                            name, {key: "fixture", "confirmed": confirmed}
                        )
                        self.assertTrue(response.is_error)
            for name, arguments in (
                ("rsi_start", {"plan_id": "plan_fixture"}),
                ("rsi_start", {"confirmed": True}),
                ("rsi_start", {"plan_id": "plan_missing", "confirmed": True}),
                ("rsi_plan", {"goal": "goal", "max_iterations": True}),
                ("rsi_plan", {"goal": "goal", "max_iterations": 0}),
                ("rsi_plan", {"goal": "goal", "max_iterations": 21}),
                ("rsi_stop", {"run_id": "../../escape"}),
            ):
                response = await session.call_tool(name, arguments)
                self.assertTrue(response.is_error)
                self.assertNotIn("SECRET", response.model_dump_json())
        self.assertFalse((self.root / "calls.txt").exists())

    async def test_status_is_bounded_and_errors_do_not_expose_private_data(self) -> None:
        async with self.client() as session:
            status = self.body(await session.call_tool("rsi_status", {}))
            self.assertEqual(len(status["runs"]), 20)
            self.assertLessEqual(len(status["runs"][0]["goal"]), 1000)
            self.assertNotIn("trajectories", status["runs"][0])
            for run_id in ("explode", "oversized"):
                response = await session.call_tool("rsi_status", {"run_id": run_id})
                self.assertTrue(response.is_error)
                self.assertNotIn("SECRET", response.model_dump_json())
                self.assertLess(len(response.model_dump_json()), 1000)
            configuration = await session.call_tool("rsi_status", {"run_id": "configuration"})
            self.assertTrue(configuration.is_error)
            self.assertIn("RSI_DATA_ROOT", configuration.model_dump_json())
            self.assertNotIn("SECRET", configuration.model_dump_json())

    async def test_closing_mcp_does_not_stop_a_detached_worker(self) -> None:
        (self.root / "spawn-worker").touch()
        pid: int | None = None
        try:
            async with self.client() as session:
                await session.call_tool("rsi_plan", {"goal": "goal", "max_iterations": 1})
                started = self.body(
                    await session.call_tool(
                        "rsi_start", {"plan_id": "plan_fixture", "confirmed": True}
                    )
                )
                pid = started["worker"]["pid"]
                self.assertIsInstance(pid, int)
            assert pid is not None
            os.kill(pid, 0)
            self.assertNotIn("stop", (self.root / "calls.txt").read_text())
        finally:
            if pid is not None:
                with suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGTERM)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--mcp-fixture":
        create_server(FixtureController(Path(sys.argv[2]))).run(transport="stdio")
    else:
        unittest.main()
