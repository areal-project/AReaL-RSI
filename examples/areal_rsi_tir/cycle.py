"""One durable TIR Harness → AReaL GRPO → release generation."""

from __future__ import annotations

import argparse
import fcntl
import json
from pathlib import Path
from typing import Literal

from areal_rsi.api.control import decode, json_bytes, read_record, safe_name
from areal_rsi.api.execution import ResearchIntent
from areal_rsi.api.records import RsiEvaluationReport, RsiSplit, TaskSetVersion
from areal_rsi.api.research import (
    CandidateRecord,
    GenerationOutcome,
    GenerationSetup,
    GenerationSpec,
    HarnessSelectionRule,
    ReleaseRecord,
    RsiTrainResult,
)
from areal_rsi.infra.coordination.policies.harness import (
    assess_harness_noise,
    select_harness,
)
from areal_rsi.infra.coordination.policies.promotion import (
    confirm_promotion,
    propose_promotion,
)
from areal_rsi.infra.coordination.releases import (
    active_release,
    publish,
    release_ancestry,
    rollback,
)
from areal_rsi.infra.storage.artifacts import (
    checkpoint_digest,
    rollout_dump_evidence,
    source_files_digest,
)
from areal_rsi.infra.storage.files import write_bytes
from areal_rsi.scripts.jobs import AReaLJobRunner, digest_bytes
from areal_rsi.scripts.slurm import check_slurm
from areal_rsi_tir.assets import tir_verifier_files
from areal_rsi_tir.contracts import TirTraceBundle
from areal_rsi_tir.evaluation import assess_tir_training_signal
from areal_rsi_tir.harness import (
    TirHarnessSpec,
    freeze_tir_harness,
    load_tir_harness,
)
from areal_rsi_tir.proposer import propose_tir_prompt
from areal_rsi_tir.tasks import load_tir_task_set, public_tir_tasks


class TirCycle:
    """One ToRL/TIR generation with an exclusive Slurm submission identity."""

    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root.resolve()

    def run(self, spec: GenerationSpec) -> GenerationOutcome:
        root = self.data_root / "runs" / safe_name(spec.run_id)
        root.mkdir(parents=True, exist_ok=True)
        with (root / ".lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self._run(spec, root)

    def _run(self, spec: GenerationSpec, root: Path) -> GenerationOutcome:
        task_set = load_tir_task_set(Path(spec.task_set_root))
        h0, h0_prompt = load_tir_harness(Path(spec.h0_root), Path(spec.runner_root))
        model_digest = checkpoint_digest(Path(spec.model_root))
        verifier_digest = source_files_digest(tir_verifier_files(Path(spec.runner_root)))
        config_file = Path(spec.areal_config)
        if config_file.is_symlink() or not config_file.is_file():
            raise ValueError("AReaL recipe config must be a regular file")
        config_bytes = config_file.read_bytes()
        setup = GenerationSetup(
            spec,
            task_set.task_set_id,
            h0.harness_id,
            model_digest,
            h0.runner_digest,
            verifier_digest,
            digest_bytes(config_bytes),
        )
        setup_path = root / "setup.json"
        if setup_path.exists() and read_record(setup_path, GenerationSetup) != setup:
            raise ValueError("Frozen generation setup changed")
        outcome_path = root / "outcome.json"
        if outcome_path.exists():
            return read_record(outcome_path, GenerationOutcome)
        active = active_release(self.data_root)
        if (
            active is not None
            and active.run_id == spec.run_id
            and active.parent_release_id == spec.parent_release_id
            and active.task_set_id == task_set.task_set_id
        ):
            parent = (
                read_record(
                    self.data_root / "releases" / f"{spec.parent_release_id}.json",
                    ReleaseRecord,
                )
                if spec.parent_release_id is not None
                else None
            )
        else:
            parent = active
        if spec.parent_release_id is None:
            if parent is not None:
                raise ValueError("Active release exists; specify its ID as the parent")
        elif (
            parent is None
            or parent.release_id != spec.parent_release_id
            or parent.model_digest != model_digest
            or parent.harness_id != h0.harness_id
        ):
            raise ValueError("Generation parent differs from the published model/Harness")
        if parent is not None:
            new_sealed = set(task_set.sealed_test.task_ids)
            new_development = {
                task_id
                for split in RsiSplit
                if split is not RsiSplit.SEALED_TEST
                for task_id in getattr(task_set, split.value).task_ids
            }
            for ancestor in release_ancestry(self.data_root, parent):
                old = load_tir_task_set(Path(ancestor.task_set_root))
                old_all = {
                    task_id for split in RsiSplit for task_id in getattr(old, split.value).task_ids
                }
                if new_sealed & old_all or new_development & set(old.sealed_test.task_ids):
                    raise ValueError("New generation reuses an ancestor sealed task")
        write_bytes(setup_path, json_bytes(setup), immutable=True)
        write_bytes(root / "areal_config.yaml", config_bytes, immutable=True)
        self._preflight(spec, root)

        jobs = AReaLJobRunner(
            root / "jobs",
            spec.run_id,
            exclude_env=(spec.researcher.api_key_env,)
            if spec.researcher is not None and spec.researcher.api_key_env is not None
            else (),
            require_slurm_jobs=True,
        )
        h0_evolve = self._evaluate(
            jobs,
            spec,
            task_set,
            h0,
            Path(spec.h0_root),
            Path(spec.model_root),
            model_digest,
            RsiSplit.HARNESS_EVOLVE,
            "h0_evolve",
            spec.seed,
            verifier_digest,
        )
        candidate_prompt = self._candidate_prompt(
            spec,
            root,
            task_set,
            h0,
            h0_prompt,
            h0_evolve,
        )
        h0_select = self._evaluate(
            jobs,
            spec,
            task_set,
            h0,
            Path(spec.h0_root),
            Path(spec.model_root),
            model_digest,
            RsiSplit.HARNESS_SELECT,
            "h0_select",
            spec.seed,
            verifier_digest,
        )
        h0_select_repeat = self._evaluate(
            jobs,
            spec,
            task_set,
            h0,
            Path(spec.h0_root),
            Path(spec.model_root),
            model_digest,
            RsiSplit.HARNESS_SELECT,
            "h0_select_repeat",
            spec.repeat_seed,
            verifier_digest,
        )
        noise = assess_harness_noise(h0_select, h0_select_repeat, spec.harness_rule.noise_band)
        write_bytes(root / "harness_noise.json", json_bytes(noise), immutable=True)
        candidate_root = root / "candidate_harness"
        if not candidate_root.exists():
            freeze_tir_harness(
                candidate_prompt,
                Path(spec.runner_root),
                candidate_root,
                max_turns=h0.max_turns,
                max_length=h0.max_length,
                tool_timeout=h0.tool_timeout,
            )
        h1, _ = load_tir_harness(candidate_root, Path(spec.runner_root))
        if h1.harness_id == h0.harness_id:
            return self._finish(
                root,
                GenerationOutcome(
                    spec.run_id,
                    "harness_rejected",
                    h0.harness_id,
                    None,
                    "Candidate prompt is identical to H0",
                    None,
                ),
            )
        h1_select = self._evaluate(
            jobs,
            spec,
            task_set,
            h1,
            candidate_root,
            Path(spec.model_root),
            model_digest,
            RsiSplit.HARNESS_SELECT,
            "h1_select",
            spec.seed,
            verifier_digest,
        )
        effective_rule = HarnessSelectionRule(
            noise.effective_noise_band, spec.harness_rule.max_relative_token_cost
        )
        choice = select_harness(h0_select, h1_select, effective_rule)
        write_bytes(root / "harness_selection.json", json_bytes(choice), immutable=True)
        if not choice.accepted:
            return self._finish(
                root,
                GenerationOutcome(
                    spec.run_id,
                    "harness_rejected",
                    h0.harness_id,
                    None,
                    choice.reason,
                    None,
                ),
            )

        primary = self._evaluate(
            jobs,
            spec,
            task_set,
            h1,
            candidate_root,
            Path(spec.model_root),
            model_digest,
            RsiSplit.HARNESS_EVOLVE,
            "signal_primary",
            spec.seed,
            verifier_digest,
        )
        repeat = self._evaluate(
            jobs,
            spec,
            task_set,
            h1,
            candidate_root,
            Path(spec.model_root),
            model_digest,
            RsiSplit.HARNESS_EVOLVE,
            "signal_repeat",
            spec.repeat_seed,
            verifier_digest,
        )
        signal = assess_tir_training_signal(
            primary,
            repeat,
            minimum_mixed_cases=spec.minimum_mixed_cases,
        )
        write_bytes(root / "training_signal.json", json_bytes(signal), immutable=True)
        if not signal.accepted:
            return self._finish(
                root,
                GenerationOutcome(
                    spec.run_id,
                    "signal_rejected",
                    h1.harness_id,
                    None,
                    "Pilot sampling showed insufficient mixed rewards",
                    None,
                ),
            )

        trained = self._train(
            jobs, spec, task_set, h1, candidate_root, model_digest, verifier_digest, root
        )
        pairs = (
            ("m0_h0", h0, Path(spec.h0_root), Path(spec.model_root), model_digest),
            ("m0_h1", h1, candidate_root, Path(spec.model_root), model_digest),
            (
                "m1_h0",
                h0,
                Path(spec.h0_root),
                Path(trained.checkpoint_path),
                trained.checkpoint_digest,
            ),
            ("m1_h1", h1, candidate_root, Path(trained.checkpoint_path), trained.checkpoint_digest),
        )
        dev = tuple(
            self._evaluate(
                jobs,
                spec,
                task_set,
                harness,
                harness_root,
                model_root,
                pair_model_digest,
                RsiSplit.MODEL_DEV,
                "dev_" + label,
                spec.seed,
                verifier_digest,
            )
            for label, harness, harness_root, model_root, pair_model_digest in pairs
        )
        proposal = propose_promotion(
            dev,
            baseline_model_digest=model_digest,
            trained_model_digest=trained.checkpoint_digest,
            baseline_harness_id=h0.harness_id,
            candidate_harness_id=h1.harness_id,
            rule=spec.promotion_rule,
        )
        if proposal is None:
            return self._finish(
                root,
                GenerationOutcome(
                    spec.run_id,
                    "dev_rejected",
                    h1.harness_id,
                    trained.checkpoint_digest,
                    "No model/Harness pair passed the pre-registered dev gate",
                    None,
                ),
            )
        write_bytes(root / "promotion_proposal.json", json_bytes(proposal), immutable=True)
        sealed = tuple(
            self._evaluate(
                jobs,
                spec,
                task_set,
                harness,
                harness_root,
                model_root,
                pair_model_digest,
                RsiSplit.SEALED_TEST,
                "sealed_" + label,
                spec.seed,
                verifier_digest,
            )
            for label, harness, harness_root, model_root, pair_model_digest in pairs
        )
        decision = confirm_promotion(
            proposal,
            sealed,
            trained_model_digest=trained.checkpoint_digest,
            candidate_harness_id=h1.harness_id,
        )
        write_bytes(root / "promotion_decision.json", json_bytes(decision), immutable=True)
        if not decision.accepted:
            return self._finish(
                root,
                GenerationOutcome(
                    spec.run_id,
                    "sealed_rejected",
                    h1.harness_id,
                    trained.checkpoint_digest,
                    decision.reason,
                    None,
                ),
            )
        selected_checkpoint = (
            trained.checkpoint_path
            if proposal.candidate_model_digest == trained.checkpoint_digest
            else spec.model_root
        )
        selected_harness_root = (
            str(candidate_root) if proposal.candidate_harness_id == h1.harness_id else spec.h0_root
        )
        release = publish(
            self.data_root,
            ReleaseRecord(
                "release_pending",
                spec.run_id,
                task_set.task_set_id,
                spec.task_set_root,
                proposal.candidate_model_digest,
                proposal.candidate_harness_id,
                selected_harness_root,
                selected_checkpoint,
                digest_bytes(json_bytes(trained)),
                digest_bytes(json_bytes(decision)),
                spec.parent_release_id,
            ),
        )

        return self._finish(
            root,
            GenerationOutcome(
                spec.run_id,
                "promoted",
                h1.harness_id,
                trained.checkpoint_digest,
                decision.reason,
                release.release_id,
            ),
        )

    @staticmethod
    def _candidate_prompt(
        spec: GenerationSpec,
        root: Path,
        task_set: TaskSetVersion,
        h0: TirHarnessSpec,
        h0_prompt: str,
        feedback: RsiEvaluationReport,
    ) -> Path:
        candidate = Path(spec.candidate_prompt)
        resolved = candidate.resolve(strict=False)
        protected_roots = (
            Path(spec.task_set_root),
            Path(spec.h0_root),
            Path(spec.model_root),
            Path(spec.runner_root),
        )
        if any(resolved.is_relative_to(path.resolve()) for path in protected_roots):
            raise ValueError("Harness candidate path overlaps a frozen input")
        record_path = root / "candidate.json"
        intent_path = root / "research.intent.json"
        feedback_digest = digest_bytes(json_bytes(feedback))
        if record_path.exists():
            record = read_record(record_path, CandidateRecord)
            if (
                candidate.is_symlink()
                or not candidate.is_file()
                or digest_bytes(candidate.read_bytes()) != record.prompt_digest
                or record.feedback_digest != feedback_digest
            ):
                raise ValueError("Frozen Harness candidate or feedback changed")
            return candidate
        if candidate.is_symlink():
            raise ValueError("Harness candidate prompt cannot be symbolic")
        source: Literal["supplied", "researcher"]
        if candidate.exists():
            if intent_path.exists():
                raise ValueError("Researcher response exists without a candidate receipt")
            source = "supplied"
        else:
            if spec.researcher is None:
                raise ValueError("No candidate prompt or researcher is configured")
            intent = ResearchIntent(
                spec.run_id,
                h0.harness_id,
                feedback_digest,
                spec.researcher.model,
                spec.researcher.endpoint,
            )
            if intent_path.exists():
                if read_record(intent_path, ResearchIntent) != intent:
                    raise ValueError("Researcher intent changed")
                raise ValueError("Researcher call is uncertain; reconcile before retry")
            write_bytes(intent_path, json_bytes(intent), immutable=True)
            prompt = propose_tir_prompt(
                spec.researcher,
                h0_prompt,
                public_tir_tasks(Path(spec.task_set_root), RsiSplit.HARNESS_EVOLVE),
                feedback,
            )
            write_bytes(candidate, prompt.encode(), immutable=True)
            source = "researcher"
        if not candidate.is_file():
            raise ValueError("Harness candidate is not a regular file")
        record = CandidateRecord(digest_bytes(candidate.read_bytes()), source, feedback_digest)
        write_bytes(record_path, json_bytes(record), immutable=True)
        return candidate

    @staticmethod
    def _finish(root: Path, outcome: GenerationOutcome) -> GenerationOutcome:
        write_bytes(root / "outcome.json", json_bytes(outcome), immutable=True)
        return outcome

    @staticmethod
    def _preflight(spec: GenerationSpec, root: Path) -> None:
        write_bytes(root / "slurm_preflight.json", json_bytes(check_slurm(spec)), immutable=True)

    @staticmethod
    def _overrides(
        spec: GenerationSpec, harness: TirHarnessSpec, model: Path, stage: str, seed: int
    ) -> tuple[str, ...]:
        task_set = Path(spec.task_set_root)
        return (
            "scheduler.type=slurm",
            f"experiment_name={spec.experiment_name}",
            f"trial_name={spec.run_id if stage == 'train' else spec.run_id + '_' + stage}",
            f"cluster.fileroot={spec.cluster_fileroot}",
            f"cluster.name_resolve.nfs_record_root={spec.name_resolve_root}",
            f"actor.path={model}",
            f"ref.path={model}",
            f"tokenizer_path={model}",
            f"sglang.model_path={model}",
            f"seed={seed}",
            "tir.enable_tools=calculator",
            f"tir.max_turns={harness.max_turns}",
            f"tir.max_length={harness.max_length}",
            f"tir.tool_timeout={harness.tool_timeout}",
            f"train_dataset.path={task_set / 'public/model_train.jsonl'}",
            f"valid_dataset.path={task_set / 'public/model_dev.jsonl'}",
        )

    def _evaluate(
        self,
        jobs: AReaLJobRunner,
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
        output = jobs.root.parent / "reports" / f"{stage}.json"
        command = (
            spec.areal_python,
            "-m",
            "areal_rsi_tir.evaluate",
            "--task-set-root",
            spec.task_set_root,
            "--harness-root",
            str(harness_root),
            "--base-model-digest",
            model_digest,
            "--split",
            split.value,
            "--output",
            str(output),
            "--config",
            str(jobs.root.parent / "areal_config.yaml"),
            *self._overrides(spec, harness, model_root, stage, seed),
            "gconfig.n_samples=1",
            "actor.reward_norm=null",
        )
        jobs.run(stage, command, output)
        report = read_record(output, RsiEvaluationReport)
        expected_ids = getattr(task_set, split.value).task_ids
        bundle_path = output.with_suffix(".trajectories.json")
        if report.trajectory_path != str(bundle_path):
            raise ValueError(f"AReaL evaluation trace path changed: {stage}")
        bundle = read_record(bundle_path, TirTraceBundle)
        bundle_bytes = bundle_path.read_bytes()
        if digest_bytes(bundle_bytes) != report.trajectory_digest:
            raise ValueError(f"AReaL evaluation traces changed: {stage}")
        if (
            report.task_set_id != task_set.task_set_id
            or report.split is not split
            or report.model_digest != model_digest
            or report.harness_id != harness.harness_id
            or report.runner_digest != harness.runner_digest
            or report.verifier_digest != verifier_digest
            or report.seed != seed
            or tuple(case.task_id for case in report.cases) != expected_ids
            or bundle.task_set_id != task_set.task_set_id
            or bundle.split is not split
            or bundle.model_digest != model_digest
            or bundle.harness_id != harness.harness_id
            or bundle.runner_digest != harness.runner_digest
            or bundle.verifier_digest != verifier_digest
            or tuple(trace.task_id for trace in bundle.trajectories) != expected_ids
            or any(
                case.generated_tokens
                != sum(
                    bool(trainable and visible)
                    for trainable, visible in zip(
                        trace.loss_mask, trace.attention_mask, strict=True
                    )
                )
                or case.score != trace.reward
                for case, trace in zip(report.cases, bundle.trajectories, strict=True)
            )
        ):
            raise ValueError(f"AReaL evaluation report failed frozen identity checks: {stage}")
        return report

    def _train(
        self,
        jobs: AReaLJobRunner,
        spec: GenerationSpec,
        task_set: TaskSetVersion,
        harness: TirHarnessSpec,
        harness_root: Path,
        model_digest: str,
        verifier_digest: str,
        root: Path,
    ) -> RsiTrainResult:
        output = root / "train.json"
        command = (
            spec.areal_python,
            "-m",
            "areal_rsi_tir.train_rl",
            "--task-set-root",
            spec.task_set_root,
            "--harness-root",
            str(harness_root),
            "--base-model-digest",
            model_digest,
            "--run-id",
            spec.run_id,
            "--receipt",
            str(output),
            "--config",
            str(root / "areal_config.yaml"),
            *self._overrides(spec, harness, Path(spec.model_root), "train", spec.seed),
            "total_train_epochs=1",
        )
        jobs.run("train", command, output)
        result = read_record(output, RsiTrainResult)
        if (
            result.run_id != spec.run_id
            or result.task_set_id != task_set.task_set_id
            or result.harness_id != harness.harness_id
            or result.base_model_digest != model_digest
            or result.runner_digest != harness.runner_digest
            or result.verifier_digest != verifier_digest
            or checkpoint_digest(Path(result.checkpoint_path)) != result.checkpoint_digest
            or rollout_dump_evidence(
                Path(result.rollout.path),
                allowed_task_ids=frozenset(task_set.model_train.task_ids),
            )
            != result.rollout
            or result.rollout.tasks != len(task_set.model_train.task_ids)
        ):
            raise ValueError("AReaL training receipt failed frozen lineage checks")
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Control one Slurm TIR RSI generation")
    operations = parser.add_subparsers(dest="operation", required=True)
    run = operations.add_parser("run")
    run.add_argument("--data-root", type=Path, required=True)
    run.add_argument("--config", type=Path, required=True)
    undo = operations.add_parser("rollback")
    undo.add_argument("--data-root", type=Path, required=True)
    undo.add_argument("--run-id", required=True)
    undo.add_argument("--expected-release-id", required=True)
    undo.add_argument("--reason", required=True)
    args = parser.parse_args()
    cycle = TirCycle(args.data_root)
    if args.operation == "rollback":
        print(
            json_bytes(
                rollback(cycle.data_root, args.run_id, args.expected_release_id, args.reason)
            ).decode()
        )
    else:
        if args.config.is_symlink() or not args.config.is_file():
            raise ValueError("Generation config must be a regular file")
        spec = decode(GenerationSpec, json.loads(args.config.read_bytes()))
        print(json_bytes(cycle.run(spec)).decode())


if __name__ == "__main__":
    main()
