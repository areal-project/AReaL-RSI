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
        assert await app.call("bootstrap", {}) == {
            "allow_simulation": False,
            "default_simulated": False,
            "has_config": True,
        }

    asyncio.run(scenario())
