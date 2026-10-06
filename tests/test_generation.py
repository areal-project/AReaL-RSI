from __future__ import annotations

import json
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
from areal_rsi.examples.classification import PROFILE as CLASSIFICATION_PROFILE
from areal_rsi.examples.tir.profile import PROFILE
from areal_rsi.infra.coordination.evaluation.harness import PromptHarnessSpec, freeze_prompt_harness
from areal_rsi.infra.coordination.evaluation.profile import PromptProfile
from areal_rsi.infra.coordination.evaluation.tasks import SplitSizes, freeze_task_set
from areal_rsi.infra.coordination.generation import GenerationCycle
from areal_rsi.infra.coordination.releases import active_release, rollback
from areal_rsi.infra.storage.artifacts import checkpoint_digest


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


@pytest.mark.parametrize(
    "profile", [PROFILE, CLASSIFICATION_PROFILE], ids=["tir", "classification"]
)
@pytest.mark.parametrize("mixed", [True, False])
def test_generation_promotes_only_after_signal_and_sealed_gate(
    frozen_inputs: tuple[Path, Path, Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mixed: bool,
    profile: PromptProfile,
) -> None:
    if profile == CLASSIFICATION_PROFILE:
        _, _, tir_runner, model = frozen_inputs
        runner = tir_runner.parents[1] / "areal"
        (runner / "workflow").mkdir()
        (runner / "workflow/rlvr.py").write_text("# pinned RLVR source\n")
        (runner / "config.yaml").write_text("scheduler:\n  type: slurm\n")
        rows = [
            {"messages": [{"role": "user", "content": question}], "answer": label}
            for question, label in (
                ("A wonderful movie", "positive"),
                ("An awful book", "negative"),
                ("A boring game", "negative"),
                ("A lovely day", "positive"),
                ("An exciting trip", "positive"),
            )
        ]
        train_source, test_source = tmp_path / "labels-train.jsonl", tmp_path / "labels-test.jsonl"
        train_source.write_text("\n".join(json.dumps(row) for row in rows[:4]) + "\n")
        test_source.write_text(json.dumps(rows[4]) + "\n")
        task_root = tmp_path / "labels"
        freeze_task_set(train_source, test_source, task_root, sizes=SplitSizes(1, 1, 1, 1), seed=42)
        prompt = tmp_path / "label-prompt.txt"
        prompt.write_text("Classify as positive or negative: {prompt}")
        h0_root = tmp_path / "label-h0"
        freeze_prompt_harness(prompt, runner, h0_root, enabled_tools=())
        frozen_inputs = task_root, h0_root, runner, model
    candidate = tmp_path / "candidate.txt"
    candidate.write_text("Reason and use the calculator. Final answer in \\boxed{{}}. {prompt}\n")
    if profile == CLASSIFICATION_PROFILE:
        candidate.write_text("Read the sentiment carefully and return only the label. {prompt}")
    spec = _spec(frozen_inputs, candidate)
    model1 = tmp_path / "trained"
    model1.mkdir()
    (model1 / "config.json").write_text('{"model_type":"gpt2"}')
    (model1 / "model.safetensors").write_bytes(b"trained")
    model1_digest = checkpoint_digest(model1)
    seen: list[str] = []

    def fake_evaluate(
        self: GenerationCycle,
        jobs: object,
        spec: GenerationSpec,
        task_set: TaskSetVersion,
        harness: PromptHarnessSpec,
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
            f"/tmp/{stage}.trajectories.json",
            "sha256:" + "4" * 64,
        )

    def fake_train(
        self: GenerationCycle,
        jobs: object,
        spec: GenerationSpec,
        task_set: TaskSetVersion,
        harness: PromptHarnessSpec,
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

    monkeypatch.setattr(GenerationCycle, "_evaluate", fake_evaluate)
    monkeypatch.setattr(GenerationCycle, "_train", fake_train)
    monkeypatch.setattr(GenerationCycle, "_preflight", staticmethod(lambda _spec, _root: None))
    cycle = GenerationCycle(tmp_path / "data", profile)
    outcome = cycle.run(spec)
    if mixed:
        assert outcome.status == "promoted"
        assert outcome.release_id is not None
        active = active_release(cycle.data_root)
        assert active is not None and active.release_id == outcome.release_id
        assert "sealed_m1_h1" in seen
        count = len(seen)
        assert cycle.run(spec) == outcome
        assert len(seen) == count
        changed_profile = replace(profile, researcher_instruction="Changed task instructions")
        with pytest.raises(ValueError, match="Immutable record changed"):
            GenerationCycle(cycle.data_root, changed_profile).run(spec)
        assert cycle.run(spec) == outcome
        next_spec = replace(
            spec,
            run_id="demo2",
            parent_release_id=outcome.release_id,
            h0_root=active.harness_root,
            model_root=active.checkpoint_path,
        )
        with pytest.raises(ValueError, match="ancestor sealed task"):
            cycle.run(next_spec)
        (cycle.data_root / "runs/demo1/outcome.json").unlink()
        assert cycle.run(spec) == outcome  # Crash after pointer update is replayable.
        event = rollback(cycle.data_root, spec.run_id, outcome.release_id, "regression")
        assert active_release(cycle.data_root) is None
        assert rollback(cycle.data_root, spec.run_id, outcome.release_id, "regression") == event
        with pytest.raises(ValueError, match="Expected release ID"):
            rollback(cycle.data_root, spec.run_id, "../active", "invalid")
    else:
        assert outcome.status == "signal_rejected"
        assert "train" not in seen
        assert all(not stage.startswith("sealed_") for stage in seen)
        assert active_release(cycle.data_root) is None
        Path(spec.areal_config).write_text("scheduler:\n  type: local\n")
        with pytest.raises(ValueError, match="setup changed"):
            cycle.run(spec)
