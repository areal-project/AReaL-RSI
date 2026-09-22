"""Framework-enforced full baseline, subset exploration and frozen full final."""

import asyncio
import hashlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from runtime_fakes import CallbackRuntime

from areal_rsi.api.research import ResearchLimits, TaskBrief, VerificationContract, VerifiedResult
from areal_rsi.base.storage import now, write_json
from areal_rsi.coordination.actions import ResearchActions
from areal_rsi.coordination.benchmark import validate_full_benchmark
from areal_rsi.coordination.config import ResearchConfig, load_config
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.coordination.jobs import ResearchExecution
from areal_rsi.coordination.verification import read_trusted_result
from areal_rsi.execution import ApprovedCommand
from areal_rsi.modules.agent_runtime import ToolCall, TurnResult
from areal_rsi.research_app import ResearchApp


def test_full_baseline_and_final_are_required_even_with_zero_score(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(
            tmp_path,
            TaskBrief(
                "Optimize",
                "Full evaluation",
                ResearchLimits(max_verify_calls=3),
                VerificationContract("score"),
            ),
        )
        source = tmp_path / "seed"
        source.mkdir()
        (source / "prompt.md").write_text("seed")
        seed = await c.store.snapshot(source, agent_id="operator", description="seed")
        c.state.update(
            seed_candidate_id=seed.candidate_id,
            baseline_candidate_id=seed.candidate_id,
            full_benchmark_evaluation=True,
            require_final_confirmation=True,
        )
        c.start()
        actions = ResearchActions(c)
        command = ApprovedCommand("verify", (sys.executable, "-c", "pass"), ())

        def reader(job: Any, candidate: Any, phase: Any) -> VerifiedResult:
            return VerifiedResult(
                job.job_id,
                candidate.candidate_id,
                c.store.contract_digest(),
                now(),
                "succeeded",
                score=0,
                passed=False,
                phase=phase,
            )

        runner = ResearchExecution(actions, verifier=command, read_verification=reader)

        async def drain() -> None:
            async with asyncio.timeout(10):
                while await runner.poll():
                    await asyncio.sleep(0.02)

        try:
            with pytest.raises(ValueError, match="full baseline"):
                c.spawn(key="early", direction="x", instruction="x")
            with pytest.raises(ValueError, match="baseline before"):
                await runner.submit(
                    "rsi",
                    ToolCall(
                        "verify_candidate",
                        {
                            "candidate_id": seed.candidate_id,
                            "phase": "search",
                        },
                        "early",
                    ),
                )
            await runner.ensure_full_baseline()
            await runner.ensure_full_baseline()
            assert len(runner.jobs) == 1
            # Persisted job identity survives owner reconstruction.
            runner.close()
            runner = ResearchExecution(actions, verifier=command, read_verification=reader)
            await runner.ensure_full_baseline()
            assert len(runner.jobs) == 1
            await drain()
            assert c.state["full_baseline_ready"] is True
            assert c.store.run().baseline_result_ref is not None
            await runner.ensure_full_baseline()
            assert len(runner.jobs) == 1
            c.spawn(key="ready", direction="x", instruction="x")
            (source / "prompt.md").write_text("candidate")
            candidate = await c.store.snapshot(source, agent_id="rsi", description="candidate")
            await runner.submit(
                "rsi",
                ToolCall(
                    "verify_candidate",
                    {
                        "candidate_id": candidate.candidate_id,
                        "phase": "search",
                    },
                    "search",
                ),
            )
            await drain()
            with pytest.raises(ValueError, match="reserved"):
                await runner.submit(
                    "rsi",
                    ToolCall(
                        "verify_candidate",
                        {
                            "candidate_id": candidate.candidate_id,
                            "phase": "search",
                        },
                        "extra",
                    ),
                )
            refused = await actions.handler("rsi")(
                ToolCall(
                    "finish",
                    {
                        "candidate_id": candidate.candidate_id,
                        "summary": "premature",
                    },
                    "premature",
                )
            )
            assert "error" in refused.output
            await runner.submit(
                "rsi",
                ToolCall(
                    "verify_candidate",
                    {
                        "candidate_id": candidate.candidate_id,
                        "phase": "final",
                    },
                    "final",
                ),
            )
            assert c.state["full_final_candidate_id"] == candidate.candidate_id
            await drain()
            result = await actions.handler("rsi")(
                ToolCall(
                    "finish",
                    {
                        "candidate_id": candidate.candidate_id,
                        "summary": "No improvement",
                    },
                    "finish",
                )
            )
            assert "error" not in result.output
            assert c.state["status"] == "succeeded"
            comparison = json.loads(
                (c.store.root / "coordination/full-comparison.json").read_text()
            )
            assert comparison["score_delta"] == 0
        finally:
            runner.close(cancel=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("tasks", [["a"], ["a", "a"], ["b", "a"]])
def test_trusted_reader_rejects_partial_duplicate_or_reordered_full_results(
    tmp_path: Path,
    tasks: list[str],
) -> None:
    from test_research_verification import fixture

    store, job, candidate, identity, result, output = fixture(tmp_path, phase="baseline")
    identity["phases"]["baseline"]["task_ids"] = ["a", "b"]
    result["task_ids"] = tasks
    write_json(output, result)
    with pytest.raises(ValueError, match="task_ids"):
        read_trusted_result(store, job, candidate, "baseline", identity=identity)


def test_policy_rejects_different_full_manifests() -> None:
    identity = {"protocol_id": "p", "manifest_id": "m", "task_ids": ["a", "b"]}
    metadata = {
        "full_benchmark_evaluation": True,
        "verification_identity": {
            "phases": {"baseline": identity, "final": {**identity, "task_ids": ["a"]}},
        },
    }
    with pytest.raises(ValueError, match="same benchmark"):
        validate_full_benchmark(metadata)


@pytest.mark.parametrize(
    "source_kind", ["content", "file", "url", "invalid", "protocol", "candidate", "nonfinite"]
)
def test_supplied_full_baseline_skips_execution_and_is_archived(
    tmp_path: Path,
    source_kind: str,
) -> None:
    async def scenario() -> None:
        c = Coordinator.create(
            tmp_path,
            TaskBrief(
                "Optimize",
                "Use supplied baseline",
                verification=VerificationContract("score"),
            ),
        )
        seed = tmp_path / "seed"
        seed.mkdir()
        (seed / "prompt.md").write_text("seed")
        candidate = await c.store.snapshot(seed, agent_id="operator", description="seed")
        identity = {"protocol_id": "p", "manifest_id": "m", "task_ids": ["a", "b"]}
        raw = {
            **identity,
            "candidate_digest": candidate.content.digest,
            "metric": "score",
            "status": "completed",
            "simulated": False,
            "score": 0,
            "passed": False,
            "metrics": {"score": 0},
        }
        source: Any = raw
        server = None
        path = tmp_path / "provided.json"
        if source_kind == "file":
            write_json(path, raw)
            source = str(path)
        elif source_kind == "url":

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self) -> None:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(json.dumps(raw).encode())

                def log_message(self, *_: Any) -> None:
                    pass

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            source = f"http://127.0.0.1:{server.server_port}/baseline.json"
        elif source_kind == "invalid":
            raw["task_ids"] = ["a"]
        elif source_kind == "protocol":
            raw["protocol_id"] = "another-protocol"
        elif source_kind == "candidate":
            raw["candidate_digest"] = "another-harness"
        elif source_kind == "nonfinite":
            raw["score"] = float("nan")
            path.write_text(json.dumps(raw))
            source = str(path)
        write_json(
            c.store.root / "coordination/config.json",
            {
                "metadata": {
                    "full_benchmark_evaluation": True,
                    "full_baseline_source": source,
                    "verification_identity": {
                        "adapter": "content",
                        "phases": {
                            "baseline": identity,
                            "final": identity,
                        },
                    },
                }
            },
        )
        c.state.update(full_benchmark_evaluation=True, baseline_candidate_id=candidate.candidate_id)
        c.start()
        runner = ResearchExecution(ResearchActions(c))  # No runnable verifier at all.
        try:
            await runner.ensure_full_baseline()
            assert runner.jobs == {}
            if source_kind in {"invalid", "protocol", "candidate", "nonfinite"}:
                assert c.state["status"] == "paused"
                assert "Cannot import full baseline" in c.state["baseline_error"]
                assert not c.state.get("full_baseline_ready")
            else:
                assert c.state["full_baseline_ready"]
                assert c.state["baseline_origin"] == "imported"
                assert (
                    json.loads((c.store.root / "coordination/imported-baseline.json").read_text())
                    == raw
                )
                if path.exists():
                    path.unlink()
                # Simulate restart after result publication but before ready-state save.
                c.state["full_baseline_ready"] = False
                await runner.ensure_full_baseline()
                assert c.state["full_baseline_ready"]
                assert runner.jobs == {}
        finally:
            runner.close(cancel=True)
            if server:
                server.shutdown()
                server.server_close()

    asyncio.run(scenario())


def test_failed_baseline_pauses_without_automatic_retry(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(
            tmp_path, TaskBrief("Optimize", "Check", verification=VerificationContract("score"))
        )
        seed = tmp_path / "seed"
        seed.mkdir()
        (seed / "prompt.md").write_text("seed")
        candidate = await c.store.snapshot(seed, agent_id="operator", description="seed")
        c.state.update(full_benchmark_evaluation=True, baseline_candidate_id=candidate.candidate_id)
        c.start()

        def reader(job: Any, candidate: Any, phase: Any) -> VerifiedResult:
            return VerifiedResult(
                job.job_id,
                candidate.candidate_id,
                c.store.contract_digest(),
                now(),
                "infra_error",
                phase=phase,
                error="missing task",
            )

        runner = ResearchExecution(
            ResearchActions(c),
            verifier=ApprovedCommand("verify", (sys.executable, "-c", "pass"), ()),
            read_verification=reader,
        )
        try:
            await runner.ensure_full_baseline()
            async with asyncio.timeout(10):
                while await runner.poll():
                    await asyncio.sleep(0.02)
            await runner.ensure_full_baseline()
            assert c.state["status"] == "paused"
            assert len(runner.jobs) == 1
            assert not c.state.get("full_baseline_ready")
        finally:
            runner.close(cancel=True)

    asyncio.run(scenario())


def test_app_imports_baseline_before_any_model_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        seed = tmp_path / "seed"
        seed.mkdir()
        (seed / "prompt.md").write_text("seed")
        files = {"prompt.md": hashlib.sha256(b"seed").hexdigest()}
        identity = {"protocol_id": "p", "manifest_id": "m", "task_ids": ["a", "b"]}
        raw = {
            **identity,
            "harness_id": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
            "status": "completed",
            "simulated": False,
            "metric": "score",
            "score": 0,
            "passed": False,
            "metrics": {"score": 0},
        }
        write_json(tmp_path / "baseline.json", raw)
        config = ResearchConfig(
            TaskBrief(
                "Optimize",
                "Check",
                ResearchLimits(max_verify_calls=1),
                VerificationContract("score"),
            ),
            seed_dir=str(seed),
            verifier=ApprovedCommand(
                "verify", (sys.executable, "-c", "raise RuntimeError('must not run')"), ()
            ),
            metadata={
                "full_benchmark_evaluation": True,
                "full_baseline_source": "baseline.json",
                "verification_identity": {
                    "adapter": "harness",
                    "phases": {"baseline": identity, "final": identity},
                },
            },
        )
        write_json(tmp_path / "config.json", config.public())
        config = load_config(tmp_path / "config.json")
        assert config.metadata["full_baseline_source"] == str(tmp_path / "baseline.json")
        requests: list[bool] = []

        async def turn(session: Path, delivery: dict[str, Any]) -> TurnResult:
            requests.append(bool(c.state.get("full_baseline_ready")))
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        app = ResearchApp(
            tmp_path / "data",
            config=config,
            runtime_factory=lambda _: CallbackRuntime(turn),
        )
        try:
            detail = await app.create({})
            c = app.coordinator(detail["run_id"])
            await app.start(c)
            c = app.coordinator(detail["run_id"])
            async with asyncio.timeout(10):
                while not requests:
                    await asyncio.sleep(0.02)
            assert all(requests)
            assert c.state["baseline_origin"] == "imported"
            assert app.active[c.store.run_id].execution.jobs == {}
        finally:
            await app.close()

    asyncio.run(scenario())
