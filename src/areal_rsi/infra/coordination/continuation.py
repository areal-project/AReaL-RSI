"""Run a frozen Claude Code RSI plan inside an existing Slurm allocation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from areal_rsi.api.control import decode, json_bytes, read_record, safe_name
from areal_rsi.api.records import AgentEvaluationReport, AgentHarnessSpec, RsiSplit
from areal_rsi.api.research import AgentHarnessSearchDecision
from areal_rsi.infra.coordination.evaluation.tasks import load_task_set
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


def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def sources() -> dict[str, str]:
    return {
        str(p.relative_to(ROOT)): digest(p)
        for folder in ("src",)
        for p in sorted((ROOT / folder).rglob("*"))
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


def prepare_config(
    plan: ContinuationPlan, output: Path, model: Path, generation: int
) -> tuple[Path, Path]:
    config = yaml.safe_load(Path(plan.template).read_text())
    trial = f"{plan.name}-g{generation}"
    config["actor"]["_version"] = "v2"
    config["rollout"]["_version"] = "v2"
    config["scheduler"]["type"] = "slurm"
    config["experiment_name"] = "rsi-continuation-20261005"
    config["trial_name"] = trial
    config["cluster"]["fileroot"] = str(output / "areal-experiments")
    config["cluster"]["name_resolve"]["nfs_record_root"] = str(output / "name-resolve")
    config["actor"]["path"] = config["ref"]["path"] = str(model)
    config["total_train_epochs"] = 2
    config["gconfig"]["max_tokens"] = plan.context_length
    config["sglang"]["context_length"] = plan.context_length
    for section, split in (("train_dataset", "model_train"), ("valid_dataset", "model_dev")):
        config[section]["path"] = str(Path(plan.task_set) / f"public/{split}.jsonl")
        config[section]["batch_size"] = 32
    config["actor"]["scheduling_spec"][0]["env_vars"]["PYTHONPATH"] = os.pathsep.join(
        (str(AREAL), str(ROOT / "src"), str(ROOT / "src"))
    )
    if plan.large_model:
        # Keep optimizer and effective batch fixed; reduce initialization/optimizer peaks.
        config["actor"]["fsdp"] = {
            "memory_efficient_load": True,
            "per_layer_optim_step": True,
        }
        config["ref"]["fsdp"] = {"memory_efficient_load": True}
        config["rollout"]["agent"]["chat_template_kwargs"] = {"enable_thinking": False}
    path = output / f"train-g{generation}.yaml"
    write_bytes(path, yaml.safe_dump(config, sort_keys=False).encode(), immutable=True)
    checkpoint = output / (
        "areal-experiments/checkpoints/root/rsi-continuation-20261005/"
        f"{trial}/default/epoch1epochstep15globalstep31"
    )
    return path, checkpoint


def decide(
    plan: ContinuationPlan, generation: int, search: Path, post: Path, checkpoint: Path
) -> RoundResult:
    task_set = load_task_set(Path(plan.task_set))
    selected = Path((search / "selected-harness-path.txt").read_text().strip())
    reused = selected == search / "incumbent-harness"
    before_path = search / ("m0-h0-dev.json" if reused else "m0-selected-dev.json")
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
    args = parser.parse_args()
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
            {
                "sources": frozen,
                "plan": digest(args.plan),
                "template": digest(Path(plan.template)),
                "task_set_id": tasks.task_set_id,
                "chat_template": digest(chat_template) if chat_template else None,
            }
        ),
        immutable=True,
    )
    env = dict(os.environ)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(key, None)
    env.update(
        {
            "PYTHONPATH": os.pathsep.join((str(AREAL), str(ROOT / "src"), str(ROOT / "src"))),
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
    model, prompt = Path(plan.model), Path(plan.prompt)
    for generation in range(1, plan.maximum_rounds + 1):
        if sources() != frozen:
            raise ValueError("Execution source changed during experiment")
        search = output / f"g{generation}-search"
        post = output / f"g{generation}-eval"
        result_path = output / f"g{generation}-result.json"
        if result_path.exists():
            result = read_record(result_path, RoundResult)
        else:
            run(
                [
                    "bash",
                    str(ROOT / "src/areal_rsi/infra/coordination/launch/run_shared512_search.sh"),
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
                "--nv",
                "--bind",
                "/storage:/storage",
                "--env",
                f"PYTHONPATH={env['PYTHONPATH']},HF_HUB_OFFLINE=1,"
                "TRANSFORMERS_OFFLINE=1,NO_PROXY=*,no_proxy=*",
                IMAGE,
                "python3",
                "-m",
                "areal_rsi.scripts.train",
                "--recipe",
                "areal_rsi.infra.coordination.evaluation.tir.claude_train.prepare",
                "--task-set-root",
                plan.task_set,
                "--harness-root",
                selected,
                "--claude-cli",
                CLAUDE,
                "--config",
                str(config),
            ]
            run([*command, "--validate-only"], env)
            log = output / f"g{generation}-train.log"
            complete = log.exists() and "Training completes!" in log.read_text()
            if not complete:
                run(command, env, log)
            if not (checkpoint / "model.safetensors").is_file():
                raise ValueError("Completed training has no checkpoint")
            for _ in range(180):
                if not subprocess.check_output(
                    ["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader"], text=True
                ).strip():
                    break
                time.sleep(2)
            else:
                raise RuntimeError("Training GPU processes did not exit")
            run(
                [
                    "bash",
                    str(ROOT / "src/areal_rsi/infra/coordination/launch/run_shared512_eval.sh"),
                    str(checkpoint),
                    plan.task_set,
                    "model_dev",
                    f"{plan.name}-g{generation}",
                    str(post),
                    str(Path(selected) / "prompt.txt"),
                ],
                env,
            )
            result = decide(plan, generation, search, post, checkpoint)
            if sources() != frozen:
                raise ValueError("Execution source changed before final report")
            write_bytes(result_path, json_bytes(result), immutable=True)
        print(json_bytes(result).decode(), flush=True)
        model, prompt = Path(result.next_model), Path(result.next_prompt)
        if result.stop:
            break


if __name__ == "__main__":
    main()
