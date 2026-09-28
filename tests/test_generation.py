from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from areal_rsi.api.records import RsiCaseScore, RsiEvaluationReport, RsiSplit, TaskSetVersion
from areal_rsi.api.research import (
    GenerationSpec,
    HarnessSelectionRule,
    RsiPromotionRule,
    RsiRolloutEvidence,
    RsiTrainResult,
)
from areal_rsi.infra.coordination.releases import active_release
from areal_rsi.infra.research import ResearchSystem
from areal_rsi.infra.tasks.tir_harness import TirHarnessSpec
from areal_rsi.infra.training.areal_tir import checkpoint_digest


def _spec(frozen: tuple[Path, Path, Path, Path], candidate: Path) -> GenerationSpec:
    task_root, h0_root, runner, model = frozen
    return GenerationSpec(
        run_id="demo1",
        task_set_root=str(task_root),
        h0_root=str(h0_root),
        candidate_prompt=str(candidate),
        runner_root=str(runner),
        model_root=str(model),
        areal_python="/usr/bin/python3",
        areal_config=str(runner / "config.yaml"),
        cluster_fileroot="/shared/experiments",
        name_resolve_root="/shared/name_resolve",
        experiment_name="demo",
        seed=42,
        repeat_seed=43,
        minimum_mixed_cases=1,
        harness_rule=HarnessSelectionRule(0, 1),
        promotion_rule=RsiPromotionRule("model_internalization", 0, 1, 1),
    )


@pytest.mark.parametrize("mixed", [True, False])
def test_generation_promotes_only_after_signal_and_sealed_gate(
    frozen_inputs: tuple[Path, Path, Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mixed: bool,
) -> None:
    candidate = tmp_path / "candidate.txt"
    candidate.write_text("Reason and use the calculator. Final answer in \\boxed{{}}. {prompt}\n")
    spec = _spec(frozen_inputs, candidate)
    model1 = tmp_path / "trained"
    model1.mkdir()
    (model1 / "config.json").write_text('{"model_type":"gpt2"}')
    (model1 / "model.safetensors").write_bytes(b"trained")
    model1_digest = checkpoint_digest(model1)
    seen: list[str] = []

    def fake_evaluate(
        self: ResearchSystem,
        jobs: object,
        spec: GenerationSpec,
        task_set: TaskSetVersion,
        harness: TirHarnessSpec,
        harness_root: Path,
        model_root: Path,
        model_digest: str,
        split: RsiSplit,
        stage: str,
        seed: int,
        verifier_digest: str,
    ) -> RsiEvaluationReport:
        del self, jobs, spec, harness_root, model_root
        seen.append(stage)
        score = int(
            stage
            in {
                "h1_select",
                "signal_primary",
                "dev_m1_h0",
                "dev_m1_h1",
                "sealed_m1_h0",
                "sealed_m1_h1",
            }
            or (stage == "signal_repeat" and not mixed)
        )
        return RsiEvaluationReport(
            model_digest,
            harness.harness_id,
            task_set.task_set_id,
            split,
            harness.runner_digest,
            verifier_digest,
            seed,
            1.0,
            512,
            tuple(
                RsiCaseScore(task_id, float(score), "answer", 8 if score else 10)
                for task_id in getattr(task_set, split.value).task_ids
            ),
        )

    def fake_train(
        self: ResearchSystem,
        jobs: object,
        spec: GenerationSpec,
        task_set: TaskSetVersion,
        harness: TirHarnessSpec,
        harness_root: Path,
        model_digest: str,
        verifier_digest: str,
        root: Path,
    ) -> RsiTrainResult:
        del self, jobs, harness_root, root
        seen.append("train")
        return RsiTrainResult(
            spec.run_id,
            "grpo",
            task_set.task_set_id,
            harness.harness_id,
            model_digest,
            model1_digest,
            harness.runner_digest,
            verifier_digest,
            "sha256:" + "1" * 64,
            str(model1),
            1,
            RsiRolloutEvidence("/shared/rollout", "sha256:" + "2" * 64, 2, 1),
        )

    monkeypatch.setattr(ResearchSystem, "_evaluate", fake_evaluate)
    monkeypatch.setattr(ResearchSystem, "_train", fake_train)
    system = ResearchSystem(tmp_path / "data")
    outcome = system.run(spec)
    if mixed:
        assert outcome.status == "promoted"
        assert outcome.release_id is not None
        active = active_release(system.data_root)
        assert active is not None and active.release_id == outcome.release_id
        assert "sealed_m1_h1" in seen
        count = len(seen)
        assert system.run(spec) == outcome
        assert len(seen) == count
        next_spec = replace(
            spec,
            run_id="demo2",
            parent_release_id=outcome.release_id,
            h0_root=active.harness_root,
            model_root=active.checkpoint_path,
        )
        with pytest.raises(ValueError, match="ancestor sealed task"):
            system.run(next_spec)
        (system.data_root / "runs/demo1/outcome.json").unlink()
        assert system.run(spec) == outcome  # Crash after pointer update is replayable.
        event = system.rollback(spec.run_id, outcome.release_id, "regression")
        assert active_release(system.data_root) is None
        assert system.rollback(spec.run_id, outcome.release_id, "regression") == event
        with pytest.raises(ValueError, match="Expected release ID"):
            system.rollback(spec.run_id, "../active", "invalid")
    else:
        assert outcome.status == "signal_rejected"
        assert "train" not in seen
        assert all(not stage.startswith("sealed_") for stage in seen)
        assert active_release(system.data_root) is None
        Path(spec.areal_config).write_text("scheduler:\n  type: local\n")
        with pytest.raises(ValueError, match="setup changed"):
            system.run(spec)
