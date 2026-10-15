"""Real scheduler/jobs/snapshots/evaluator, with explicitly substituted model boundaries."""

from __future__ import annotations

import asyncio
import json
import math
import sys
from dataclasses import replace
from itertools import combinations, pairwise
from pathlib import Path
from typing import Any

import pytest
from runtime_fakes import CallbackRuntime

from areal_rsi.api.interfaces import ToolCall, TurnResult
from areal_rsi.api.research import CandidateSnapshot, VerifiedResult
from areal_rsi.infra.adapters.benchmark_research import math_loop_history, prepare
from areal_rsi.infra.coordination.config import load_config
from areal_rsi.infra.research import ResearchSystem
from areal_rsi.infra.tasks.counting import count_paths


def test_counting_oracle_against_enumeration_and_peak_formula() -> None:
    for n, ceiling, peaks, modulus in ((5, 3, 2, 3), (6, 4, 3, 7), (4, 4, 1, 2)):
        expected = 0
        for ups in combinations(range(2 * n), n):
            path = [1 if i in ups else -1 for i in range(2 * n)]
            heights = [0]
            for step in path:
                heights.append(heights[-1] + step)
            expected += int(
                min(heights) >= 0
                and max(heights) <= ceiling
                and sum(a == 1 and b == -1 for a, b in pairwise(path)) == peaks
                and sum(heights[:-1]) % modulus == 0
            )
        assert count_paths(n, ceiling, peaks, modulus) == expected
    # With no height/residue restriction the count is the Narayana number.
    assert count_paths(18, 18, 5, 1) == math.comb(18, 5) * math.comb(18, 4) // 18
    assert count_paths() > 0


def test_three_version_loop_accepts_zero_accuracy_and_survives_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RSI_API_KEY", "offline-fixture")
    monkeypatch.setenv("RSI_MODEL", "fixture")
    monkeypatch.setenv("RSI_BASE_URL", "http://127.0.0.1:9/v1/responses")
    for key in ("MODEL", "BASE_URL", "API_KEY"):
        monkeypatch.delenv("RSI_TARGET_" + key, raising=False)
    # Only task generation is substituted. The actual adapter runs in four real subprocesses.
    verifier_script = tmp_path / "model_fixture.py"
    verifier_script.write_text("""
from areal_rsi.api.records import TaskResult, Trajectory
from areal_rsi.infra.adapters.benchmark_research import main
from areal_rsi.infra.tasks.harness import LocalHarnessRuntime
from areal_rsi.infra.storage.files import FileTrajectoryStore, now

async def run(self, solution, task, *, config=None):
    trace = await FileTrajectoryStore(
        self.content.root.parent / 'trajectories', self.content
    ).save(Trajectory(task.task_id, 'fixture',
                     await self.content.put_json({'fixture': True}), {}, now()))
    return TaskResult(await self.content.put_json({
        'solution_id': solution.solution_id, 'task_id': task.task_id,
        'response': '#### -1'}), trace)

LocalHarnessRuntime.run = run
main()
""")

    async def scenario() -> None:
        path = await prepare("math-loop", tmp_path / "setup")
        config = load_config(path)
        manifest = json.loads(path.with_name("manifest.json").read_text())
        assert manifest["target_source"] == "research"
        assert manifest["target"]["model"] == "fixture"
        assert manifest["target"]["base_url"] == "http://127.0.0.1:9/v1"
        assert "offline-fixture" not in path.read_text()
        assert config.verifier is not None
        config.verifier = replace(
            config.verifier,
            command=(sys.executable, str(verifier_script), *config.verifier.command[3:]),
        )
        saved_ids: list[str] = []

        async def respond(session: Path, delivery: dict[str, Any]) -> TurnResult:
            handler = delivery["handler"]
            stage = len(runtime.calls) - 1
            c = system.coordinator(run_id)
            results = sorted(
                c.store.records("verifications", VerifiedResult), key=lambda r: r.created_at
            )
            assert len(results) == stage
            assert all(r.status == "succeeded" and r.score == 0 for r in results)
            if stage < 3:
                if stage:
                    # One or two measured versions cannot pass final, even if an agent claims so.
                    with pytest.raises(ValueError, match="Math loop needs"):
                        math_loop_history(c.store, saved_ids[-1])
                    previous = results[-1]
                    ref = c.store.paths.reference(
                        c.store.root / "verifications" / f"{previous.result_id}.json"
                    ).uri
                    lesson = await handler(
                        ToolCall(
                            "save_evidence",
                            {
                                "title": f"Lesson {stage}",
                                "summary": "Wrong result: add an independent boundary check.",
                                "references": [ref],
                                "limitations": "Model output is an offline fixture",
                            },
                            f"lesson-{stage}",
                        )
                    )
                    assert "error" not in lesson.output, lesson
                    if stage == 1:
                        # A redundant save must not count as an iteration or poison later ancestry.
                        duplicate_save = await handler(
                            ToolCall(
                                "save_candidate",
                                {"description": "accidental unchanged save"},
                                "duplicate-save",
                            )
                        )
                        assert "error" not in duplicate_save.output
                    prompt = delivery["workspace"] / "prompt.md"
                    prompt.write_text(prompt.read_text() + f"\nCheck boundary case {stage}.\n")
                saved = await handler(
                    ToolCall("save_candidate", {"description": f"v{stage}"}, f"save-{stage}")
                )
                saved_ids.append(saved.output["candidate_id"])
                result = await handler(
                    ToolCall(
                        "verify_candidate",
                        {
                            "candidate_id": saved_ids[-1],
                            "phase": "diagnostic" if stage == 0 else "search",
                            "wait": True,
                        },
                        f"verify-{stage}",
                    )
                )
            elif stage == 3:
                history = math_loop_history(c.store, saved_ids[1])
                assert [i.candidate.candidate_id for i in history] == saved_ids
                # Identical content under another candidate ID is not another iteration.
                original = history[-1].candidate
                with monkeypatch.context() as patch:
                    records = c.store.records

                    def duplicate(category: str, kind: Any) -> Any:
                        values = records(category, kind)
                        if kind is CandidateSnapshot:
                            return [
                                replace(v, content=history[1].candidate.content)
                                if v.candidate_id == original.candidate_id
                                else v
                                for v in values
                            ]
                        return values

                    patch.setattr(c.store, "records", duplicate)
                    with pytest.raises(ValueError, match="distinct contents"):
                        math_loop_history(c.store, saved_ids[1])
                with monkeypatch.context() as patch:
                    records = c.store.records

                    def without_lessons(category: str, kind: Any) -> Any:
                        return [] if category == "evidence" else records(category, kind)

                    patch.setattr(c.store, "records", without_lessons)
                    with pytest.raises(ValueError, match="save_evidence"):
                        math_loop_history(c.store, saved_ids[1])
                # Choosing the earlier improvement is valid; final must confirm that exact version.
                result = await handler(
                    ToolCall(
                        "verify_candidate",
                        {
                            "candidate_id": saved_ids[1],
                            "phase": "final",
                            "wait": True,
                        },
                        "final",
                    )
                )
            else:
                assert stage == 4
                result = await handler(
                    ToolCall(
                        "update_goal",
                        {
                            "status": "completed",
                            "summary": "Three versions checked; all scores zero",
                            "current_step": "done",
                            "next_steps": [],
                            "blocker": "",
                            "acceptance": config.brief.acceptance,
                            "evidence": [r.result_id for r in results],
                            "candidate_id": saved_ids[1],
                        },
                        "accept",
                    )
                )
            assert not result.output.get("error"), result
            assert result.stop
            return TurnResult(session.name, delivery["message_id"], "", result.reason, {})

        runtime = CallbackRuntime(respond)
        system = ResearchSystem(tmp_path / "data", runtime_factory=lambda _: runtime)
        try:
            run_id = await system.create(config)
            await system.start(run_id)
            active = system.active[run_id]
            active.execution.inline_wait_seconds = 0
            await asyncio.wait_for(active.task, 25)
            c = system.coordinator(run_id)
            assert c.state.status == "succeeded", await system.detail(run_id)
            assert len(active.execution.jobs) == 4
            reports = list(c.store.root.glob("execution-outputs/*/public/loop.json"))
            assert len(reports) == 1
            report = json.loads(reports[0].read_text())
            assert report["final_score"] == 0 and len(report["iterations"]) == 3
            await system.close()
            system = ResearchSystem(tmp_path / "data", runtime_factory=lambda _: runtime)
            assert system.coordinator(run_id).state.status == "succeeded"
            await system.export(run_id, tmp_path / "export")
            exported = (tmp_path / "export/prompt.md").read_text()
            assert "boundary case 1" in exported and "boundary case 2" not in exported
        finally:
            await system.close()

    asyncio.run(scenario())
