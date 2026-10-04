"""Freeze the paired post-loop synthetic experiment and compare its reports."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from areal_rsi.api.control import decode, json_bytes, read_record
from areal_rsi.api.records import AgentEvaluationReport, RsiSplit
from areal_rsi.infra.storage.files import write_bytes
from areal_rsi_tir.claude_code import load_claude_harness
from areal_rsi_tir.synthesis import (
    SyntheticMathCase,
    freeze_tir_synthetic_probe,
    freeze_tir_synthetic_training,
    select_model_calibrated_cases,
    synthesize_tir_math_cases,
)
from areal_rsi_tir.tasks import load_tir_task_set

SYNTH_ROOT = Path(__file__).resolve().parents[2]
ROOT = SYNTH_ROOT / "data/synthetic-experiment"
CLAUDE = Path("/storage/openpsi/users/fenghui/claude")
EXPERIMENT = "rsi-claude-synthetic-paired"
SYNTHETIC_SEED = 20261002
PROBE_CASES = 3072
TRAIN_CASES = 512
PLAN = ROOT / "plan-v1.json"


@dataclass(frozen=True, slots=True)
class RetainedState:
    terminal_generation: int
    parent_generation: int
    decision_path: str
    model_root: str
    harness_root: str
    task_set_root: str


@dataclass(frozen=True, slots=True)
class PreparedExperiment:
    retained: RetainedState
    synthetic_task_set_root: str
    control_config: str
    synthetic_config: str
    control_checkpoint: str
    synthetic_checkpoint: str
    prompt_digest: str


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return "sha256:" + h.hexdigest()


def retained_state() -> RetainedState:
    pilot_state = os.environ.get("RSI_RETAINED_STATE_PATH")
    if pilot_state:
        state = decode(RetainedState, json.loads(Path(pilot_state).read_bytes()))
        decision = json.loads(Path(state.decision_path).read_bytes())
        generation = state.terminal_generation
        action = decision.get("decision")
        if (
            not 1 <= generation <= 6
            or decision.get("generation") != generation
            or action not in ("stop_weight_loop", "complete_six_generations")
            or (action == "complete_six_generations" and generation != 6)
            or state.parent_generation
            != (generation - 1 if action == "stop_weight_loop" else generation)
            or decision.get("retained_model_root") != state.model_root
            or decision.get("retained_harness_root") != state.harness_root
        ):
            raise ValueError("Synthesis requires the retained state of a terminal weight loop")
        model = Path(state.model_root).resolve(strict=True)
        harness = Path(state.harness_root).resolve(strict=True)
        if not (model / "model.safetensors").is_file():
            raise ValueError("Retained pilot checkpoint is incomplete")
        load_claude_harness(harness, CLAUDE, model)
        task_set = load_tir_task_set(Path(state.task_set_root))
        if decision.get("task_set_id") != task_set.task_set_id:
            raise ValueError("Retained loop task set differs from its decision")
        return state
    raise ValueError("RSI_RETAINED_STATE_PATH must identify the terminal loop state")


def source_digests() -> dict[str, str]:
    paths = subprocess.check_output(
        ["git", "ls-files", "src", "examples/areal_rsi_tir"],
        cwd=SYNTH_ROOT,
        text=True,
    ).splitlines()
    result = {name: digest(SYNTH_ROOT / name) for name in paths}
    for name in (
        "examples/areal_rsi_tir/run_synthetic_paired.sh",
        "examples/areal_rsi_tir/run_synthetic_eval.sh",
    ):
        path = SYNTH_ROOT / name
        if path.is_file():
            result[name] = digest(path)
    return result


def prepare_probe() -> None:
    state = retained_state()
    parent = load_tir_task_set(Path(state.task_set_root))
    if len(parent.model_train.task_ids) != TRAIN_CASES:
        raise ValueError("The paired 512-case experiment needs a frozen 512-case parent task set")
    excluded = (Path(state.task_set_root),)
    root = ROOT / "probe-taskset"
    if not root.exists():
        freeze_tir_synthetic_probe(
            Path(state.task_set_root),
            root,
            seed=SYNTHETIC_SEED,
            count=PROBE_CASES,
            excluded_task_set_roots=excluded,
        )
    probe = load_tir_task_set(root)
    candidates = synthesize_tir_math_cases(seed=SYNTHETIC_SEED, count=PROBE_CASES)
    if probe.harness_evolve.task_ids != tuple(item.case.task_id for item in candidates):
        raise ValueError("Frozen difficulty probe differs from the generator")
    write_bytes(
        ROOT / "probe-state.json",
        json_bytes(
            {
                "model_root": state.model_root,
                "harness_root": state.harness_root,
                "task_set_root": str(root),
                "prompt_path": str(Path(state.harness_root) / "prompt.txt"),
                "probe_task_set_id": probe.task_set_id,
            }
        ),
        immutable=True,
    )


def calibrated_cases() -> tuple[tuple[SyntheticMathCase, ...], tuple[str, str], tuple[int, int]]:
    state = retained_state()
    probe_root = ROOT / "probe-taskset"
    probe = load_tir_task_set(probe_root)
    candidates = synthesize_tir_math_cases(seed=SYNTHETIC_SEED, count=PROBE_CASES)
    expected_ids = tuple(item.case.task_id for item in candidates)
    if probe.harness_evolve.task_ids != expected_ids:
        raise ValueError("Difficulty probe candidate IDs changed")
    reports = tuple(
        read_record(ROOT / f"probe-{name}/report.json", AgentEvaluationReport)
        for name in ("first", "second")
    )
    harness, _ = load_claude_harness(Path(state.harness_root), CLAUDE, Path(state.model_root))
    for report in reports:
        if (
            report.split is not RsiSplit.HARNESS_EVOLVE
            or report.task_set_id != probe.task_set_id
            or report.model != state.model_root
            or report.harness_id != harness.harness_id
            or tuple(case.task_id for case in report.cases) != expected_ids
            or any(case.score not in (0.0, 1.0) for case in report.cases)
        ):
            raise ValueError("Difficulty probe changed frozen model, Harness or questions")
    if (
        reports[0].verifier_digest != reports[1].verifier_digest
        or reports[0].executable_digest != reports[1].executable_digest
        or reports[0].backend_model_digest != reports[1].backend_model_digest
    ):
        raise ValueError("Difficulty probe runs used different execution inputs")
    successes = tuple(
        int(first.score + second.score)
        if first.failure_reason is None and second.failure_reason is None
        else None
        for first, second in zip(reports[0].cases, reports[1].cases, strict=True)
    )
    selected = select_model_calibrated_cases(
        candidates, successes, count=TRAIN_CASES, seed=SYNTHETIC_SEED
    )
    digests = (
        digest(ROOT / "probe-first/report.json"),
        digest(ROOT / "probe-second/report.json"),
    )
    return selected.cases, digests, (selected.zero_passes, selected.one_pass)


def arm_config(arm: str, state: RetainedState, task_root: Path) -> tuple[Path, Path]:
    task_set = load_tir_task_set(task_root)
    if len(task_set.model_train.task_ids) != TRAIN_CASES:
        raise ValueError("Both paired arms must contain exactly 512 training questions")
    config = yaml.safe_load(Path(os.environ["RSI_TRAIN_TEMPLATE"]).read_text())
    if (
        config["total_train_epochs"] != 2
        or config["saver"]["freq_epochs"] != 2
        or config["train_dataset"]["batch_size"] != 32
        or config["gconfig"]["n_samples"] != 4
        or config["gconfig"]["max_new_tokens"] != 512
        or config["actor"]["optimizer"]["lr"] != 1.7e-5
    ):
        raise ValueError("Base recipe no longer matches the frozen paired plan")
    trial = f"shared512-g{state.parent_generation}-{arm}-20261004"
    config["experiment_name"] = EXPERIMENT
    config["trial_name"] = trial
    config["total_train_epochs"] = 2
    config["saver"]["freq_epochs"] = 2
    config["cluster"]["fileroot"] = str(ROOT / "areal-experiments")
    config["cluster"]["name_resolve"]["nfs_record_root"] = str(ROOT / "name-resolve")
    config["actor"]["path"] = state.model_root
    config["ref"]["path"] = state.model_root
    config["train_dataset"]["path"] = str(task_root / "public/model_train.jsonl")
    config["valid_dataset"]["path"] = str(task_root / "public/model_dev.jsonl")
    config["actor"]["scheduling_spec"][0]["env_vars"]["PYTHONPATH"] = ":".join(
        (
            "/storage/openpsi/users/fenghui/projects/AReaL-dev-0928",
            str(SYNTH_ROOT / "src"),
            str(SYNTH_ROOT / "examples"),
        )
    )
    output = ROOT / f"{arm}.yaml"
    write_bytes(output, yaml.safe_dump(config, sort_keys=False).encode(), immutable=True)
    checkpoint = (
        ROOT
        / "areal-experiments/checkpoints/root"
        / EXPERIMENT
        / trial
        / "default/epoch1epochstep15globalstep31"
    )
    return output, checkpoint


def prepare() -> PreparedExperiment:
    state = retained_state()
    selected, probe_digests, pass_counts = calibrated_cases()
    excluded = (Path(state.task_set_root),)
    synthetic_root = ROOT / "taskset"
    if not synthetic_root.exists():
        freeze_tir_synthetic_training(
            Path(state.task_set_root),
            synthetic_root,
            selected,
            seed=SYNTHETIC_SEED,
            probe_task_set_id=load_tir_task_set(ROOT / "probe-taskset").task_set_id,
            probe_report_digests=probe_digests,
            selected_pass_counts=pass_counts,
            excluded_task_set_roots=excluded,
        )
    synthetic_set = load_tir_task_set(synthetic_root)
    parent_set = load_tir_task_set(Path(state.task_set_root))
    if (
        synthetic_set.model_dev != parent_set.model_dev
        or synthetic_set.sealed_test != parent_set.sealed_test
    ):
        raise ValueError("Synthetic task set changed the fixed evaluation cases")
    provenance = json.loads((synthetic_root / "synthetic-provenance.json").read_bytes())
    if (
        provenance["parent_task_set_id"] != parent_set.task_set_id
        or provenance["synthetic_train_cases"] != 512
        or provenance["original_train_cases"] != 0
        or provenance["probe_report_digests"] != list(probe_digests)
    ):
        raise ValueError("Synthetic task set differs from the paired plan")
    control_config, control_checkpoint = arm_config("control", state, Path(state.task_set_root))
    synthetic_config, synthetic_checkpoint = arm_config("synthetic", state, synthetic_root)
    harness, _ = load_claude_harness(Path(state.harness_root), CLAUDE, Path(state.model_root))
    prepared = PreparedExperiment(
        state,
        str(synthetic_root),
        str(control_config),
        str(synthetic_config),
        str(control_checkpoint),
        str(synthetic_checkpoint),
        harness.prompt_digest,
    )
    write_bytes(ROOT / "prepared.json", json_bytes(prepared), immutable=True)
    source = source_digests()
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=SYNTH_ROOT, text=True
    ).strip()
    for arm, config, task_set in (
        ("control", control_config, parent_set),
        ("synthetic", synthetic_config, synthetic_set),
    ):
        lock = {
            "arm": arm,
            "base_commit": commit,
            "source_digests": source,
            "experiment_plan_digest": digest(PLAN),
            "terminal_decision_digest": digest(Path(state.decision_path)),
            "task_set_id": task_set.task_set_id,
            "task_set_root": state.task_set_root if arm == "control" else str(synthetic_root),
            "retained_model_root": state.model_root,
            "retained_harness_root": state.harness_root,
            "retained_harness_id": harness.harness_id,
            "config": str(config),
            "config_digest": digest(config),
        }
        write_bytes(ROOT / f"{arm}-source-lock.json", json_bytes(lock), immutable=True)
    return prepared


def load_prepared() -> PreparedExperiment:
    return decode(PreparedExperiment, json.loads((ROOT / "prepared.json").read_bytes()))


def verify_locks() -> None:
    prepared = load_prepared()
    current_sources = source_digests()
    for arm, config in (
        ("control", prepared.control_config),
        ("synthetic", prepared.synthetic_config),
    ):
        lock = json.loads((ROOT / f"{arm}-source-lock.json").read_bytes())
        if (
            lock["source_digests"] != current_sources
            or lock["config_digest"] != digest(Path(config))
            or lock["terminal_decision_digest"] != digest(Path(prepared.retained.decision_path))
            or lock["experiment_plan_digest"] != digest(PLAN)
        ):
            raise ValueError(f"{arm} source lock changed")


def compare_reports(split: RsiSplit) -> tuple[AgentEvaluationReport, AgentEvaluationReport]:
    prepared = load_prepared()
    expected_ids = getattr(
        load_tir_task_set(Path(prepared.retained.task_set_root)), split.value
    ).task_ids
    expected_task_sets = (
        load_tir_task_set(Path(prepared.retained.task_set_root)).task_set_id,
        load_tir_task_set(Path(prepared.synthetic_task_set_root)).task_set_id,
    )
    reports = tuple(
        read_record(ROOT / arm / split.value / "report.json", AgentEvaluationReport)
        for arm in ("control", "synthetic")
    )
    for report, task_set_id, checkpoint, arm in zip(
        reports,
        expected_task_sets,
        (prepared.control_checkpoint, prepared.synthetic_checkpoint),
        ("control", "synthetic"),
        strict=True,
    ):
        manifest = json.loads((ROOT / arm / split.value / "harness/manifest.json").read_bytes())
        if (
            report.split is not split
            or report.task_set_id != task_set_id
            or tuple(case.task_id for case in report.cases) != expected_ids
            or report.model != checkpoint
            or manifest["prompt_digest"] != prepared.prompt_digest
            or report.harness_id != manifest["harness_id"]
        ):
            raise ValueError(f"{arm} changed frozen {split.value} evaluation inputs")
    if (
        reports[0].verifier_digest != reports[1].verifier_digest
        or reports[0].executable_digest != reports[1].executable_digest
    ):
        raise ValueError("Paired reports used different verifier or Claude Code executable")
    return reports  # type: ignore[return-value]


def decide_dev() -> None:
    verify_locks()
    control, synthetic = compare_reports(RsiSplit.MODEL_DEV)
    control_correct = sum(case.score for case in control.cases)
    synthetic_correct = sum(case.score for case in synthetic.cases)
    decision = {
        "control_correct": control_correct,
        "synthetic_correct": synthetic_correct,
        "development_cases": len(control.cases),
        "paired_synthetic_minus_control": (synthetic_correct - control_correct)
        / len(control.cases),
        "control_report_digest": digest(ROOT / "control/model_dev/report.json"),
        "synthetic_report_digest": digest(ROOT / "synthetic/model_dev/report.json"),
    }
    write_bytes(ROOT / "dev-decision.json", json_bytes(decision), immutable=True)


def finalize() -> None:
    verify_locks()
    decision_path = ROOT / "dev-decision.json"
    decision = json.loads(decision_path.read_bytes())
    if decision["control_report_digest"] != digest(
        ROOT / "control/model_dev/report.json"
    ) or decision["synthetic_report_digest"] != digest(ROOT / "synthetic/model_dev/report.json"):
        raise ValueError("The frozen test decision inputs changed")
    control, synthetic = compare_reports(RsiSplit.MODEL_DEV)
    result = {
        "retained_generation": load_prepared().retained.parent_generation,
        "postloop_training_task_set_generation": load_prepared().retained.terminal_generation,
        "test_cases": len(control.cases),
        "test_control_correct": sum(case.score for case in control.cases),
        "test_synthetic_correct": sum(case.score for case in synthetic.cases),
        "paired_verifier_digest": control.verifier_digest,
        "test_decision_digest": digest(decision_path),
        "test_control_report_digest": digest(ROOT / "control/model_dev/report.json"),
        "test_synthetic_report_digest": digest(ROOT / "synthetic/model_dev/report.json"),
    }
    write_bytes(ROOT / "final-comparison.json", json_bytes(result), immutable=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=("prepare-probe", "prepare", "verify", "decide-dev", "finalize")
    )
    args = parser.parse_args()
    if args.action == "prepare-probe":
        prepare_probe()
        print((ROOT / "probe-state.json").read_text())
    elif args.action == "prepare":
        print(json_bytes(prepare()).decode())
    elif args.action == "verify":
        verify_locks()
        print("source locks valid")
    elif args.action == "decide-dev":
        decide_dev()
        print((ROOT / "dev-decision.json").read_text())
    else:
        finalize()
        print((ROOT / "final-comparison.json").read_text())


if __name__ == "__main__":
    main()
