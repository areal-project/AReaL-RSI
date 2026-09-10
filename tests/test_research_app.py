"""Application acceptance across durable records, real tools and public exports."""

from __future__ import annotations

import asyncio
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from areal_rsi.api.research import TaskBrief, VerificationContract, VerifiedResult
from areal_rsi.base.storage import now
from areal_rsi.coordination.config import ResearchConfig
from areal_rsi.modules.responses import ResponsesConfig, ResponsesRuntime
from areal_rsi.research_app import ResearchApp


class WaitingTransport:
    def __init__(self) -> None:
        self.calls = 0

    async def request(self, config: Any, payload: Any, sink: Any) -> dict[str, Any]:
        self.calls += 1
        return {
            "id": f"response_{self.calls}",
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "name": "wait",
                    "arguments": '{"reason":"Waiting for an external research input"}',
                    "call_id": f"wait_{self.calls}",
                }
            ],
            "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
        }


def test_archiving_maintenance_chat_preserves_delivery_and_original_records(tmp_path: Path) -> None:
    async def scenario() -> None:
        from areal_rsi.api.research import ResearchMessage

        app = ResearchApp(tmp_path, config=ResearchConfig(TaskBrief("研究", "继续当前任务")))
        detail = await app.call("create", {"simulated": False})
        c = app.coordinator(detail["run_id"])
        hidden = c.user_message("维护期间的补充", key="maintenance", recipient="interaction")
        visible = c.enqueue("runtime", "user", "研究已恢复", key="notice")
        c.state["archived_chat_message_ids"] = [hidden]
        c.save()
        public = await app.detail(c.store.run_id)
        assert [m["message_id"] for m in public["messages"]] == [visible]
        assert c.store.get("messages", hidden, ResearchMessage).text == "维护期间的补充"
        delivered, _ = c.begin_turn("interaction")
        assert delivered.message_id == hidden
        await app.close()

    asyncio.run(scenario())


def test_no_baseline_waiting_and_restart_keep_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        transport = WaitingTransport()
        config = ResearchConfig(TaskBrief("Investigate", "No starting product or score required"))

        def runtime(_: ResearchConfig) -> ResponsesRuntime:
            return ResponsesRuntime(
                ResponsesConfig(base_url="https://example.test", api_key="test"), transport
            )

        app = ResearchApp(tmp_path, config=config, runtime_factory=runtime)
        detail = await app.call("create", {"simulated": False})
        run_id = detail["run_id"]
        assert detail["baseline"] is detail["best"] is None
        assert detail["status"] == "planned" and transport.calls == 0
        await app.call("start", {"run_id": run_id})
        for _ in range(100):
            if transport.calls and not app.active[run_id].engine.active_agents:
                break
            await asyncio.sleep(0.01)
        assert transport.calls == 1
        await asyncio.sleep(0.1)
        assert transport.calls == 1
        observer = ResearchApp(tmp_path, config=config, runtime_factory=runtime)
        assert (await observer.detail(run_id))["worker_alive"]
        with pytest.raises(ValueError, match="another live process"):
            await observer.call("pause", {"run_id": run_id})
        assert app.coordinator(run_id).state["status"] == "running"
        session = app.coordinator(run_id).actor("rsi").session_id
        await app.close()
        resumed = ResearchApp(tmp_path, config=config, runtime_factory=runtime)
        try:
            await resumed.call("resume", {"run_id": run_id})
            await asyncio.sleep(0.1)
            assert transport.calls == 1
            assert resumed.coordinator(run_id).actor("rsi").session_id == session
            await resumed.call("pause", {"run_id": run_id})
            assert (await resumed.detail(run_id))["status"] == "paused"
            await resumed.call("stop", {"run_id": run_id})
            assert (await resumed.detail(run_id))["status"] == "stopped"
        finally:
            await resumed.close()

    asyncio.run(scenario())


def test_interaction_reply_is_archived_and_visible_without_polling_model(tmp_path: Path) -> None:
    class ReplyTransport(WaitingTransport):
        async def request(self, config: Any, payload: Any, sink: Any) -> dict[str, Any]:
            packet = await super().request(config, payload, sink)
            packet["output"] = [
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "已记录：只修改 skills。"}],
                }
            ]
            return packet

    async def scenario() -> None:
        transport = ReplyTransport()
        config = ResearchConfig(TaskBrief("Improve", "Discuss the allowed scope"))
        app = ResearchApp(
            tmp_path,
            config=config,
            runtime_factory=lambda _: ResponsesRuntime(
                ResponsesConfig(base_url="https://example.test", api_key="test"), transport
            ),
        )
        run_id = (await app.create({"simulated": False}))["run_id"]
        try:
            await app.call("chat", {"run_id": run_id, "text": "只修改 skills", "message_id": "one"})
            for _ in range(100):
                detail = await app.detail(run_id)
                if len(detail["messages"]) == 2:
                    break
                await asyncio.sleep(0.01)
            assert [m["text"] for m in detail["messages"]] == [
                "只修改 skills",
                "已记录：只修改 skills。",
            ]
            assert detail["status"] == "planned"
            for _ in range(3):
                await app.detail(run_id)
            assert transport.calls == 1
        finally:
            await app.close()

    asyncio.run(scenario())


def test_export_never_attaches_another_candidates_score(tmp_path: Path) -> None:
    async def scenario() -> None:
        seed = tmp_path / "seed"
        seed.mkdir()
        (seed / "model.py").write_text("first")
        app = ResearchApp(
            tmp_path / "data",
            config=ResearchConfig(
                TaskBrief(
                    "Improve",
                    "Preserve correct score identity",
                    verification=VerificationContract("accuracy"),
                ),
                seed_dir=str(seed),
            ),
            allow_simulation=True,
        )
        detail = await app.create({"simulated": True})
        c = app.coordinator(detail["run_id"])
        first = c.state["seed_candidate_id"]
        c.store.record_verification(
            VerifiedResult(
                "best",
                first,
                c.store.contract_digest(),
                now(),
                "succeeded",
                0.8,
                True,
                simulated=True,
            )
        )
        c.store.consider_best("best", simulated_run=True)
        (seed / "model.py").write_text("second, unverified")
        other = await c.store.snapshot(seed, agent_id="operator", description="Unverified")
        download = await app.call(
            "export", {"run_id": c.store.run_id, "candidate_id": other.candidate_id}
        )
        with zipfile.ZipFile(download.path) as archive:
            result = json.loads(archive.read("research-result.json"))
            assert result["candidate_id"] == other.candidate_id
            assert result["verification"] is None
            assert archive.read("candidate/model.py") == b"second, unverified"
        c.state["selected_candidate_id"] = other.candidate_id
        c.save()
        selected = await app.call("export", {"run_id": c.store.run_id})
        with zipfile.ZipFile(selected.path) as archive:
            assert (
                json.loads(archive.read("research-result.json"))["candidate_id"]
                == other.candidate_id
            )
        await app.close()

    asyncio.run(scenario())


def test_real_verification_requires_identity_and_failed_create_leaves_no_run(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        from areal_rsi.execution import ApprovedCommand

        app = ResearchApp(
            tmp_path,
            config=ResearchConfig(
                TaskBrief("Check", "Do not stamp an unknown protocol"),
                verifier=ApprovedCommand("verify", ("operator-verifier",)),
            ),
        )
        with pytest.raises(ValueError, match="verification_identity"):
            await app.create({"simulated": False})
        assert not (tmp_path / "runs").exists()
        app.default_config = ResearchConfig(
            TaskBrief("Check", "Missing seed"), seed_dir=str(tmp_path / "missing")
        )
        with pytest.raises(FileNotFoundError, match="Seed directory"):
            await app.create({"simulated": False})
        assert not (tmp_path / "runs").exists()
        bootstrap = await app.call("bootstrap", {})
        assert {
            key: bootstrap[key] for key in ("allow_simulation", "default_simulated", "has_config")
        } == {
            "allow_simulation": False,
            "default_simulated": False,
            "has_config": True,
        }

    asyncio.run(scenario())


def test_standalone_baseline_job_exposes_live_progress_without_creating_a_node(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        from areal_rsi.base.storage import write_json
        from areal_rsi.execution.models import JobRecord, JobSpec

        app = ResearchApp(tmp_path, config=ResearchConfig(TaskBrief("Research", "Run work")))
        run_id = (await app.create({"simulated": False}))["run_id"]
        c = app.coordinator(run_id)
        c.state["baseline_candidate_id"] = "seed"
        c.save()
        output = c.store.root / "execution-outputs" / "baseline"
        output.mkdir(parents=True)
        write_json(output / "progress.json", {"completed": 1, "total": 8, "stage": "评测中"})
        info = {
            "agent_id": "rsi",
            "candidate_id": "seed",
            "iteration_id": None,
            "verification": True,
            "phase": "search",
            "output": str(output),
        }
        write_json(c.store.root / "coordination" / "jobs.json", {"baseline": info})
        write_json(
            c.store.root / "jobs" / "baseline" / "job.json",
            JobRecord(
                "baseline",
                JobSpec(),
                "running",
                100.0,
                101.0,
                started_at=101.0,
            ),
        )
        detail = await app.detail(run_id)
        job = detail["jobs"][0]
        assert job["title"] == "起点评测" and job["is_baseline"] is True
        assert job["agent_name"] == "RSI Agent" and job["iteration_id"] is None
        assert job["progress"] == {"completed": 1, "total": 8, "stage": "评测中"}
        assert detail["nodes"] == [] and detail["best"] is None
        for phase, candidate, title in [
            ("final", "seed", "最终验证"),
            ("search", "next", "迭代验证"),
            ("diagnostic", "seed", "诊断检查"),
        ]:
            write_json(
                c.store.root / "coordination" / "jobs.json",
                {
                    "baseline": {**info, "phase": phase, "candidate_id": candidate},
                },
            )
            assert (await app.detail(run_id))["jobs"][0]["title"] == title
        await app.close()

    asyncio.run(scenario())
