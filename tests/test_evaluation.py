from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from areal_rsi_tir.assets import (
    checkpoint_digest,
    source_files_digest,
    tir_verifier_files,
)
from areal_rsi_tir.contracts import TirTraceBundle, TirTrajectory
from areal_rsi_tir.cycle import TirCycle
from areal_rsi_tir.evaluation import (
    complete_tir_report,
    tir_trajectory,
    verified_tir_case,
)
from areal_rsi_tir.harness import load_tir_harness
from areal_rsi_tir.tasks import load_tir_task_set

from areal_rsi.api.control import json_bytes, read_record
from areal_rsi.api.records import RsiCaseScore, RsiSplit
from areal_rsi.api.research import GenerationSpec, HarnessSelectionRule, RsiPromotionRule
from areal_rsi.scripts.jobs import AReaLJobRunner, digest_bytes


def test_tir_verification_excludes_tool_observation_tokens() -> None:
    trajectory = {
        "input_ids": [[1, 2, 3, 4]],
        "loss_mask": [[0, 1, 0, 1]],
        "attention_mask": [[True, True, True, True]],
        "rewards": [1.0],
    }

    def verify(response: str, answer: str) -> float:
        assert answer == "24"
        return float(response == answer)

    case = verified_tir_case(
        "case1",
        trajectory,
        answer="24",
        decode=lambda ids: "".join(str(token) for token in ids),
        verify=verify,
    )
    assert case.response == "24"
    assert case.generated_tokens == 2
    assert case.score == 1.0
    evidence = tir_trajectory("case1", trajectory)
    assert evidence.input_ids == (1, 2, 3, 4)
    assert evidence.loss_mask == (0, 1, 0, 1)
    assert evidence.attention_mask == (1, 1, 1, 1)
    trajectory["rewards"] = [0.0]
    with pytest.raises(ValueError, match="disagrees"):
        verified_tir_case(
            "case1",
            trajectory,
            answer="24",
            decode=lambda ids: "".join(str(token) for token in ids),
            verify=verify,
        )


def test_trace_bundle_preserves_binary_masks_without_gold_answers(tmp_path: Path) -> None:
    localized = {
        "input_ids": [[1, 2, 3]],
        "loss_mask": [[0, 1, 0]],
        "attention_mask": [[True, True, True]],
        "rewards": [0.0],
    }
    trace = tir_trajectory("case1", localized)
    bundle = TirTraceBundle(
        1,
        "taskset_t0",
        RsiSplit.HARNESS_EVOLVE,
        "sha256:" + "1" * 64,
        "harness_h0",
        "sha256:" + "2" * 64,
        "sha256:" + "3" * 64,
        (trace,),
    )
    encoded = json_bytes(bundle)
    assert b"answer" not in encoded
    assert json.loads(encoded)["trajectories"][0]["attention_mask"] == [1, 1, 1]
    path = tmp_path / "trace.json"
    path.write_bytes(encoded)
    assert read_record(path, TirTraceBundle) == bundle
    assert digest_bytes(encoded).startswith("sha256:")
    path.write_bytes(json_bytes(replace(bundle, trajectories=(replace(trace, reward=1.0),))))
    assert read_record(path, TirTraceBundle) != bundle
    assert digest_bytes(path.read_bytes()) != digest_bytes(encoded)


@pytest.mark.parametrize("tampered", [False, True])
def test_controller_rejects_a_report_with_changed_trace_bundle(
    frozen_inputs: tuple[Path, Path, Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tampered: bool,
) -> None:
    task_root, h0_root, runner, model = frozen_inputs
    task_set = load_tir_task_set(task_root)
    harness, _ = load_tir_harness(h0_root, runner)
    model_digest = checkpoint_digest(model)
    verifier_digest = source_files_digest(tir_verifier_files(runner))
    candidate = tmp_path / "candidate.txt"
    candidate.write_text("Candidate {prompt}")
    spec = GenerationSpec(
        "run1",
        str(task_root),
        str(h0_root),
        str(candidate),
        str(runner),
        str(model),
        "/usr/bin/python3",
        str(runner / "config.yaml"),
        "/shared/experiments",
        "/shared/name_resolve",
        "demo",
        1,
        2,
        1,
        HarnessSelectionRule(0, 1),
        RsiPromotionRule("joint_performance", 0, 1, 1),
    )
    jobs = AReaLJobRunner(tmp_path / "run/jobs", "run1")
    task_id = task_set.harness_evolve.task_ids[0]

    def fake_run(stage: str, command: tuple[str, ...], output: Path) -> Path:
        del stage, command
        bundle_path = output.with_suffix(".trajectories.json")
        trace = TirTrajectory(task_id, (1, 2), (0, 1), (1, 1), 0.0)
        bundle = TirTraceBundle(
            1,
            task_set.task_set_id,
            RsiSplit.HARNESS_EVOLVE,
            model_digest,
            harness.harness_id,
            harness.runner_digest,
            verifier_digest,
            (trace,),
        )
        encoded = json_bytes(bundle)
        bundle_path.parent.mkdir(parents=True, exist_ok=True)
        bundle_path.write_bytes(encoded)
        report = complete_tir_report(
            task_set,
            RsiSplit.HARNESS_EVOLVE,
            (RsiCaseScore(task_id, 0.0, "wrong", 1),),
            model_digest=model_digest,
            harness_id=harness.harness_id,
            runner_digest=harness.runner_digest,
            verifier_digest=verifier_digest,
            seed=1,
            temperature=1.0,
            max_new_tokens=32,
            trajectory_path=str(bundle_path),
            trajectory_digest=digest_bytes(encoded),
        )
        output.write_bytes(json_bytes(report))
        if tampered:
            bundle_path.write_bytes(
                json_bytes(replace(bundle, trajectories=(replace(trace, reward=1.0),)))
            )
        return output

    monkeypatch.setattr(jobs, "run", fake_run)

    def action() -> object:
        return TirCycle(tmp_path / "data")._evaluate(
            jobs,
            spec,
            task_set,
            harness,
            h0_root,
            model,
            model_digest,
            RsiSplit.HARNESS_EVOLVE,
            "baseline",
            1,
            verifier_digest,
        )

    if tampered:
        with pytest.raises(ValueError, match="traces changed"):
            action()
    else:
        assert action().cases[0].score == 0.0
