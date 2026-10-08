"""Run a frozen Claude Code RSI plan from a CPU Slurm controller."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from areal_rsi.api.control import decode, json_bytes, read_record, safe_name
from areal_rsi.api.records import AgentEvaluationReport, AgentHarnessSpec, RsiSplit
from areal_rsi.api.research import AgentHarnessSearchDecision
from areal_rsi.examples.tir.claude_solver import (
    SavedEvaluation,
    freeze_claude_harness,
    reverify_claude_report,
)
from areal_rsi.infra.coordination.evaluation.tasks import load_task_set
from areal_rsi.infra.storage.artifacts import checkpoint_digest, validate_hf_model_files
from areal_rsi.infra.storage.files import write_bytes

ROOT = Path(os.environ.get("RSI_ROOT", str(Path.cwd()))).resolve()
AREAL = Path("/storage/openpsi/users/fenghui/projects/AReaL-dev-0928")
IMAGE = "/storage/openpsi/images/areal-dev-sglang-0821.sif"
CLAUDE = "/storage/openpsi/users/fenghui/claude"


@dataclass(frozen=True, slots=True)
class ContinuationPlan:
    name: str
    model: str
    prompt: str
    task_set: str
    template: str
    minimum_rounds: int
    maximum_rounds: int
    stop_drop: float
    eval_memory_fraction: float
    large_model: bool
    context_length: int

    def __post_init__(self) -> None:
        safe_name(self.name)
        if not 4096 <= self.context_length <= 32768:
            raise ValueError("Invalid context length")
        if not 1 <= self.minimum_rounds <= self.maximum_rounds <= 6:
            raise ValueError("Invalid round limits")
        if not 0 < self.stop_drop < 1 or not 0 < self.eval_memory_fraction < 1:
            raise ValueError("Invalid evaluation or stopping policy")


@dataclass(frozen=True, slots=True)
class RoundResult:
    generation: int
    before_correct: float
    after_correct: float
    test_cases: int
    candidate_correct: tuple[float, ...]
    reused_incumbent: bool
    stop: bool
    next_model: str
    next_prompt: str
    report_digests: dict[str, str]


@dataclass(frozen=True, slots=True)
class ContinuationSources:
    sources: dict[str, str]
    plan: str
    template: str
    task_set_id: str
    chat_template: str | None
    training_evaluation: bool = True


@dataclass(frozen=True, slots=True)
class CompletedFirstTraining:
    """Read-only provenance for a completed training stage awaiting evaluation."""

    source_root: str
    output_root: str
    plan_digest: str
    source_lock_digest: str
    config_digest: str
    log_digest: str
    checkpoint_digest: str
    search_digests: dict[str, str]


def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def sources(root: Path = ROOT) -> dict[str, str]:
    return {
        str(p.relative_to(root)): digest(p)
        for folder in ("src",)
        for p in sorted((root / folder).rglob("*"))
        if p.suffix in (".py", ".sh")
    }


def run(command: list[str], env: dict[str, str], log: Path | None = None) -> None:
    if log is None:
        subprocess.run(command, cwd=ROOT, env=env, check=True)
    else:
        # Never erase evidence of an interrupted training attempt.
        if log.exists():
            raise ValueError(f"Existing incomplete attempt requires a new output directory: {log}")
        with log.open("wb") as stream:
            subprocess.run(command, cwd=ROOT, env=env, stdout=stream, stderr=stream, check=True)


def run_gpu(command: list[str], env: dict[str, str], *, gpus: int = 1) -> None:
    # A CPU controller's allocation cannot host GPU steps. Start a fresh allocation.
    allocation_env = {key: value for key, value in env.items() if not key.startswith("SLURM_")}
    allocation_env.pop("SBATCH_RESERVATION", None)
    allocation_env["SLURM_EXPORT_ENV"] = "ALL"
    reservation = env.get("RSI_SLURM_RESERVATION")
    run(
        [
            "srun",
            "--mpi=none",
            "--nodes=1",
            "--ntasks=1",
            "--cpus-per-task=8",
            "--mem=64G",
            f"--gres=gpu:{gpus}",
            "--time=08:00:00",
            f"--partition={env['RSI_SLURM_PARTITION']}",
            *([f"--reservation={reservation}"] if reservation else []),
            "--job-name=rsi-gpu-stage",
            *command,
        ],
        allocation_env,
    )


def training_config(
    plan: ContinuationPlan,
    output: Path,
    model: Path,
    generation: int,
    *,
    source_root: Path = ROOT,
) -> dict[str, Any]:
    """Render the native AReaL YAML boundary for launch or provenance verification."""

    config = decode(dict[str, Any], yaml.safe_load(Path(plan.template).read_text()))
    trial = f"{plan.name}-g{generation}"
    config["actor"]["_version"] = "v2"
    config["rollout"]["_version"] = "v2"
    config["scheduler"]["type"] = "slurm"
    # Native Slurm schedules the two d4 roles independently, four GPUs per job.
    config["cluster"]["n_gpus_per_node"] = 4
    config["experiment_name"] = "rsi-continuation-20261005"
    config["trial_name"] = trial
    config["cluster"]["fileroot"] = str(output / "areal-experiments")
    config["cluster"]["name_resolve"]["nfs_record_root"] = str(output / "name-resolve")
    config["actor"]["path"] = config["ref"]["path"] = str(model)
    config["rollout"]["model"] = str(model)
    config["total_train_epochs"] = 2
    config["gconfig"]["max_tokens"] = plan.context_length
    config["sglang"]["context_length"] = plan.context_length
    for section, split in (("train_dataset", "model_train"), ("valid_dataset", "model_dev")):
        config[section]["path"] = str(Path(plan.task_set) / f"public/{split}.jsonl")
        config[section]["batch_size"] = 32
    scheduling = config["actor"]["scheduling_spec"][0]
    scheduling["reservation"] = os.environ.get("RSI_SLURM_RESERVATION") or None
    scheduling["srun_additional_args"] = "--unbuffered --mpi=none -K"
    scheduling["env_vars"]["PYTHONPATH"] = os.pathsep.join((str(AREAL), str(source_root / "src")))
    if plan.large_model:
        config["rollout"]["agent"]["chat_template_kwargs"] = {"enable_thinking": False}
    return config


def prepare_config(
    plan: ContinuationPlan, output: Path, model: Path, generation: int
) -> tuple[Path, Path]:
    config = training_config(plan, output, model, generation)
    trial = config["trial_name"]
    path = output / f"train-g{generation}.yaml"
    write_bytes(path, yaml.safe_dump(config, sort_keys=False).encode(), immutable=True)
    checkpoint = output / (
        "areal-experiments/checkpoints/root/rsi-continuation-20261005/"
        f"{trial}/default/epoch1epochstep15globalstep31"
    )
    return path, checkpoint


def search_digests(search: Path) -> dict[str, str]:
    return {
        path.relative_to(search).as_posix(): digest(path)
        for path in sorted(search.rglob("*"))
        if path.is_file()
        and path.suffix in (".json", ".txt")
        and not any(part.endswith(".cases") for part in path.relative_to(search).parts)
    }


def reuse_first_training(
    plan: ContinuationPlan, plan_path: Path, receipt: CompletedFirstTraining
) -> tuple[Path, Path]:
    """Verify saved training without writing to its source, search or checkpoint."""

    output = Path(receipt.output_root).resolve(strict=True)
    lock_path = output / "source-lock.json"
    config_path, log_path = output / "train-g1.yaml", output / "g1-train.log"
    if (
        digest(plan_path) != receipt.plan_digest
        or digest(lock_path) != receipt.source_lock_digest
        or digest(config_path) != receipt.config_digest
        or digest(log_path) != receipt.log_digest
    ):
        raise ValueError("Completed training provenance changed")
    lock = read_record(lock_path, ContinuationSources)
    if (
        lock.plan != receipt.plan_digest
        or lock.template != digest(Path(plan.template))
        or lock.task_set_id != load_task_set(Path(plan.task_set)).task_set_id
        or lock.chat_template
        != (digest(output / "nonthinking.jinja") if plan.large_model else None)
        or not lock.sources
        or lock.sources != sources(Path(receipt.source_root))
    ):
        raise ValueError("Completed training source or inputs changed")
    config = yaml.safe_load(config_path.read_bytes())
    expected = training_config(
        plan, output, Path(plan.model), 1, source_root=Path(receipt.source_root)
    )
    if config != expected:
        raise ValueError("Completed training configuration differs from the requested round")
    log = log_path.read_text()
    if (
        "Training completes!" not in log
        or "final training step (global_step=31)" not in log
        or "Weight update completed for pair 'actor-rollout' v32" not in log
    ):
        raise ValueError("First training round has not completed all 32 updates")
    search = output / "g1-search"
    if (
        not (search / "selected-harness-path.txt").is_file()
        or not (search / "model-candidates/decision.json").is_file()
        or not receipt.search_digests
        or receipt.search_digests != search_digests(search)
    ):
        raise ValueError("Completed training search evidence changed or is incomplete")
    checkpoint = output / (
        "areal-experiments/checkpoints/root/rsi-continuation-20261005/"
        f"{plan.name}-g1/default/epoch1epochstep15globalstep31"
    )
    validate_hf_model_files(checkpoint)
    if checkpoint_digest(checkpoint) != receipt.checkpoint_digest:
        raise ValueError("Completed training checkpoint changed")
    return search, checkpoint


def decide(
    plan: ContinuationPlan,
    generation: int,
    search: Path,
    post: Path,
    checkpoint: Path,
    *,
    before_path: Path | None = None,
) -> RoundResult:
    task_set = load_task_set(Path(plan.task_set))
    selected = Path((search / "selected-harness-path.txt").read_text().strip())
    reused = selected == search / "incumbent-harness"
    before_path = before_path or search / ("m0-h0-dev.json" if reused else "m0-selected-dev.json")
    after_path = post / "report.json"
    before = read_record(before_path, AgentEvaluationReport)
    after = read_record(after_path, AgentEvaluationReport)
    pre_harness = read_record(selected / "manifest.json", AgentHarnessSpec)
    post_harness = read_record(post / "harness/manifest.json", AgentHarnessSpec)
    selection = read_record(search / "model-candidates/decision.json", AgentHarnessSearchDecision)
    for report in (before, after):
        if (
            report.split is not RsiSplit.MODEL_DEV
            or report.task_set_id != task_set.task_set_id
            or tuple(c.task_id for c in report.cases) != task_set.model_dev.task_ids
            or report.executable_digest != before.executable_digest
            or report.verifier_digest != before.verifier_digest
        ):
            raise ValueError("Frozen evaluation inputs differ")
    if (
        before.harness_id != pre_harness.harness_id
        or after.harness_id != post_harness.harness_id
        or pre_harness.prompt_digest != post_harness.prompt_digest
        or before.backend_model_digest == after.backend_model_digest
        or after.model != str(checkpoint)
        or selection.selected_harness_id != before.harness_id
    ):
        raise ValueError("Selected harness or checkpoint identity differs")
    before_correct = sum(c.score for c in before.cases)
    after_correct = sum(c.score for c in after.cases)
    count = len(after.cases)
    drop = (after_correct - before_correct) / count < -plan.stop_drop
    stop = generation >= plan.minimum_rounds and drop
    candidates = tuple(
        sum(c.score for c in read_record(p, AgentEvaluationReport).cases)
        for p in sorted((search / "model-candidates").glob("candidate-*/selection.json"))
    )
    return RoundResult(
        generation,
        before_correct,
        after_correct,
        count,
        candidates,
        reused,
        stop,
        before.model if stop else after.model,
        str((selected if stop else post / "harness") / "prompt.txt"),
        {"before": digest(before_path), "after": digest(after_path)},
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reuse-first-training", type=Path)
    parser.add_argument("--reuse-first-evaluation", type=Path)
    parser.add_argument("--no-training-eval", action="store_true")
    args = parser.parse_args()
    if args.reuse_first_evaluation is not None and args.reuse_first_training is None:
        parser.error("--reuse-first-evaluation requires --reuse-first-training")
    plan = read_record(args.plan, ContinuationPlan)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    tasks = load_task_set(Path(plan.task_set))
    if not (
        tasks.harness_evolve == tasks.harness_select == tasks.model_train
        and len(tasks.model_train.task_ids) == 512
        and len(tasks.model_dev.task_ids) == 256
    ):
        raise ValueError("Expected shared train512 and test256")
    chat_template = None
    if plan.large_model:
        # Match AReaL rollout rendering despite SGLang Anthropic ignoring template kwargs.
        tokenizer_config = json.loads((Path(plan.model) / "tokenizer_config.json").read_bytes())
        template = decode(str, tokenizer_config["chat_template"])
        chat_template = output / "nonthinking.jinja"
        write_bytes(
            chat_template,
            ("{%- set enable_thinking = false -%}\n" + template).encode(),
            immutable=True,
        )
    frozen = sources()
    write_bytes(
        output / "source-lock.json",
        json_bytes(
            ContinuationSources(
                frozen,
                digest(args.plan),
                digest(Path(plan.template)),
                tasks.task_set_id,
                digest(chat_template) if chat_template else None,
                training_evaluation=not args.no_training_eval,
            )
        ),
        immutable=True,
    )
    env = dict(os.environ)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(key, None)
    env.update(
        {
            "PYTHONPATH": os.pathsep.join((str(AREAL), str(ROOT / "src"))),
            "NO_PROXY": "*",
            "no_proxy": "*",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "RSI_EVAL_MEMORY_FRACTION": str(plan.eval_memory_fraction),
            "RSI_EVAL_CONTEXT_LENGTH": str(plan.context_length),
        }
    )
    if chat_template is not None:
        env["RSI_EVAL_CHAT_TEMPLATE"] = str(chat_template)
    reuse_paths = None
    reuse = None
    saved_evaluation = None
    if args.reuse_first_training is not None:
        reuse = read_record(args.reuse_first_training, CompletedFirstTraining)
        reuse_paths = reuse_first_training(plan, args.plan, reuse)
        # The new execution records references; parent records stay in place.
        write_bytes(output / "g1-training-reuse.json", json_bytes(reuse), immutable=True)
    if args.reuse_first_evaluation is not None:
        saved_evaluation = read_record(args.reuse_first_evaluation, SavedEvaluation)
    model, prompt = Path(plan.model), Path(plan.prompt)
    for generation in range(1, plan.maximum_rounds + 1):
        if sources() != frozen:
            raise ValueError("Execution source changed during experiment")
        search = output / f"g{generation}-search"
        post = output / f"g{generation}-eval"
        result_path = output / f"g{generation}-result.json"
        before_path = None
        restored_evaluation = False
        if result_path.exists():
            result = read_record(result_path, RoundResult)
        else:
            if generation == 1 and reuse_paths is not None:
                search, checkpoint = reuse_paths
                if reuse is not None and saved_evaluation is not None:
                    selected = Path((search / "selected-harness-path.txt").read_text().strip())
                    original_before = (
                        "m0-h0-dev.json"
                        if selected == search / "incumbent-harness"
                        else "m0-selected-dev.json"
                    )
                    before_path = output / "g1-before-reverified.json"
                    reverify_claude_report(
                        SavedEvaluation(
                            str(search / original_before),
                            reuse.search_digests[original_before],
                            reuse.source_root,
                        ),
                        Path(plan.task_set),
                        before_path,
                        runner_root=AREAL / "areal",
                    )
                    if not (post / "harness").exists():
                        freeze_claude_harness(
                            selected / "prompt.txt",
                            Path(CLAUDE),
                            checkpoint,
                            post / "harness",
                            model=str(checkpoint),
                        )
                    reverify_claude_report(
                        saved_evaluation,
                        Path(plan.task_set),
                        post / "report.json",
                        runner_root=AREAL / "areal",
                    )
                    restored_evaluation = True
            else:
                run_gpu(
                    [
                        "bash",
                        str(ROOT / "src/areal_rsi/examples/tir/launch/run_shared512_search.sh"),
                        str(model),
                        plan.task_set,
                        str(prompt),
                        str(search),
                    ],
                    env,
                )
                selected = (search / "selected-harness-path.txt").read_text().strip()
                config, checkpoint = prepare_config(plan, output, model, generation)
                command = [
                    "singularity",
                    "exec",
                    "--no-eval",
                    "--pid",
                    "--writable-tmpfs",
                    "--bind",
                    env["CONTROLLER_MOUNTS"],
                    "--env",
                    f"PYTHONPATH={env['PYTHONPATH']},HF_HUB_OFFLINE=1,"
                    "TRANSFORMERS_OFFLINE=1,NO_PROXY=*,no_proxy=*,LD_LIBRARY_PATH=/usr/lib64/slurm",
                    IMAGE,
                    "python3",
                    "-m",
                    "areal_rsi.scripts.train",
                    "--recipe",
                    "areal_rsi.examples.tir.claude_train.prepare",
                    "--task-set-root",
                    plan.task_set,
                    "--harness-root",
                    selected,
                    "--claude-cli",
                    CLAUDE,
                    "--config",
                    str(config),
                ]
                if args.no_training_eval:
                    command.append("--no-training-eval")
                run([*command, "--validate-only"], env)
                log = output / f"g{generation}-train.log"
                complete = log.exists() and "Training completes!" in log.read_text()
                if not complete:
                    # Wait for the eight-GPU budget before AReaL's bounded worker startup.
                    run_gpu(["true"], env, gpus=8)
                    run(command, env, log)
                validate_hf_model_files(checkpoint)
            selected = (search / "selected-harness-path.txt").read_text().strip()
            if not restored_evaluation:
                run_gpu(
                    [
                        "bash",
                        str(ROOT / "src/areal_rsi/examples/tir/launch/run_shared512_eval.sh"),
                        str(checkpoint),
                        plan.task_set,
                        "model_dev",
                        f"{plan.name}-g{generation}",
                        str(post),
                        str(Path(selected) / "prompt.txt"),
                    ],
                    env,
                )
            result = decide(plan, generation, search, post, checkpoint, before_path=before_path)
            if sources() != frozen:
                raise ValueError("Execution source changed before final report")
            write_bytes(result_path, json_bytes(result), immutable=True)
        print(json_bytes(result).decode(), flush=True)
        model, prompt = Path(result.next_model), Path(result.next_prompt)
        if result.stop:
            break


if __name__ == "__main__":
    main()
