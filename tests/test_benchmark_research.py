"""ResearchSystem benchmark jobs preserve candidate, task and grading identities."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from test_envarena import FakeArena

from areal_rsi.api.records import SolutionVersion, Task, TaskResult, Trajectory
from areal_rsi.infra.adapters import benchmark_research as adapter
from areal_rsi.infra.coordination.config import load_config
from areal_rsi.infra.coordination.coordinator import Coordinator
from areal_rsi.infra.coordination.evaluation.verification import read_trusted_result
from areal_rsi.infra.execution import JobRecord
from areal_rsi.infra.storage.files import FileTrajectoryStore, now, write_json
from areal_rsi.infra.tasks.harness import LocalHarnessRuntime
from areal_rsi.infra.tasks.remote.envarena.client import ArenaConnection


@pytest.mark.parametrize("benchmark", ["math", "lcb"])
def test_research_candidate_is_evaluated_by_fixed_benchmark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, benchmark: str
) -> None:
    for key, value in {
        "RSI_API_KEY": "fixture-secret",
        "RSI_TARGET_MODEL": "fixture",
        "RSI_TARGET_BASE_URL": "https://target.example/v1",
        "RSI_TARGET_API_KEY": "target-secret",
    }.items():
        monkeypatch.setenv(key, value)
    client = FakeArena()
    monkeypatch.setattr(adapter, "ArenaClient", lambda _: client)
    monkeypatch.setattr(ArenaConnection, "from_environment", lambda: client.connection)

    async def task_run(
        self: LocalHarnessRuntime,
        solution: SolutionVersion,
        task: Task,
        *,
        config: dict[str, Any] | None = None,
    ) -> TaskResult:
        # Explicit model fixture; this test does not claim a real benchmark score.
        answers = {
            "train_0": 21,
            "train_1": 112,
            "train_2": 56,
            "validate_0": 9,
            "validate_1": 60,
            "validate_2": 94,
        }
        trace = await FileTrajectoryStore(
            self.content.root.parent / "trajectories", self.content
        ).save(
            Trajectory(
                task.task_id,
                "fixture-task",
                await self.content.put_json({"fixture": True}),
                {},
                now(),
            )
        )
        return TaskResult(
            await self.content.put_json(
                {
                    "solution_id": solution.solution_id,
                    "task_id": task.task_id,
                    "response": f"#### {answers[task.task_id]}",
                }
            ),
            trace,
        )

    monkeypatch.setattr(LocalHarnessRuntime, "run", task_run)

    async def scenario() -> None:
        arena = tmp_path / "arena.yaml"
        write_json(
            arena,
            {
                "benchmark": "base@1.0.0",
                "harness": "base@1.0.0",
                "model": "base@1.0.0",
                "train_cases": ["train-a", "train-b"],
                "validate_cases": ["validate"],
                "allowed_changes": ["system_prompt"],
                "poll_seconds": 0.01,
            },
        )
        path = await adapter.prepare(benchmark, tmp_path / "setup", arena_config=arena)
        config = load_config(path)
        assert "fixture-secret" not in path.read_text()
        assert "target-secret" not in path.read_text()
        assert "validate_0" not in (path.parent / "public-train.json").read_text()
        c = Coordinator.create(tmp_path / "data", config.brief)
        seed = Path(config.seed_dir or "")
        baseline = await c.store.snapshot(seed, agent_id="operator", description="baseline")
        with (seed / "prompt.md").open("a") as stream:
            stream.write("\nCheck all requirements before finalizing.\n")
        candidate = await c.store.snapshot(
            seed,
            agent_id="research",
            description="candidate",
            parent_candidate_id=baseline.candidate_id,
        )
        manifest_path = path.parent / "manifest.json"
        identity = adapter.manifest_identity(json.loads(manifest_path.read_text()))
        assert config.verifier is not None
        for phase in ("diagnostic", "search", "final"):
            job_id = "job-" + phase
            workspace = c.store.root / "execution-inputs" / job_id / "workspace"
            await c.store.content.materialize(candidate.content, workspace)
            write_json(workspace.parent / "snapshot.json", candidate.content)
            output = c.store.root / "execution-outputs" / job_id
            metadata = dict(
                candidate_id=candidate.candidate_id,
                candidate_digest=candidate.content.digest,
                contract_digest=c.store.contract_digest(),
                phase=phase,
            )
            spec = config.verifier.bind(
                {"workspace": str(workspace), "output": str(output), "phase": phase},
                cwd=str(workspace),
                **metadata,
            )
            job = JobRecord(job_id, spec, "succeeded", 1, 1, exit_code=0)
            write_json(c.store.root / "jobs" / job_id / "job.json", job)
            result = await adapter.evaluate(manifest_path, identity, workspace, output, phase)
            assert result["split"] == ("validate" if phase == "final" else "train")
            verified = read_trusted_result(
                c.store, job, candidate, phase, identity=config.metadata["verification_identity"]
            )
            assert verified.status == "succeeded"
            assert verified.passed is True, "Partial accuracy must remain a comparable result"
            assert verified.score == (1.0 if benchmark == "math" else 0.5)
            assert result["candidate_digest"] == candidate.content.digest
            if phase != "final":
                assert len(list((output / "public").glob("*.json"))) > 1
            else:
                assert list(p.name for p in (output / "public").glob("*.json")) == ["feedback.json"]
        if benchmark == "lcb":
            assert len(client.jobs) == 3
            assert all(
                j["model_ref"] == {"key": "base", "version": "1.0.0"} for j in client.jobs.values()
            )
        manifest = json.loads(manifest_path.read_text())
        manifest["target"] = {"model": "changed"}
        write_json(manifest_path, manifest)
        with pytest.raises(ValueError, match="manifest changed"):
            await adapter.evaluate(manifest_path, identity, workspace, output, "final")

    asyncio.run(scenario())
