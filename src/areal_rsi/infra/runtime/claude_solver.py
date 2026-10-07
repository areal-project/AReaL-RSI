"""Evaluate a frozen Claude Code solving Harness on public ToRL questions."""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import json
import logging
import re
import stat
import subprocess
import sys
import unicodedata
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, replace
from multiprocessing import get_context
from pathlib import Path
from typing import Any, Literal

from areal_rsi.api.control import decode, json_bytes, read_record, safe_name
from areal_rsi.api.execution import McpTool
from areal_rsi.api.records import (
    AgentCaseRecord,
    AgentEvaluationReport,
    AgentHarnessSpec,
    RsiSplit,
    Task,
)
from areal_rsi.api.research import AgentHarnessAttemptFailure, AgentHarnessSearchDecision
from areal_rsi.infra.coordination.evaluation.assets import _gold_answer, verifier_sources
from areal_rsi.infra.coordination.evaluation.harness import validate_prompt
from areal_rsi.infra.coordination.evaluation.tasks import load_task_set, public_tasks
from areal_rsi.infra.coordination.evaluation.tir.verifier import strict_math_reward
from areal_rsi.infra.coordination.policies.harness import select_agent_harnesses
from areal_rsi.infra.coordination.tools import calculator as calculator_tool
from areal_rsi.infra.runtime.anthropic import complete_instruction
from areal_rsi.infra.runtime.claude import (
    CLAUDE_MAX_OUTPUT_TOKENS,
    ClaudeBudgetLimit,
    ClaudeOutputLimit,
    final_event,
    read_result,
    run_claude,
)
from areal_rsi.infra.storage.artifacts import source_files_digest
from areal_rsi.infra.storage.files import write_bytes
from areal_rsi.scripts.jobs import digest_bytes


def _claude_math_reward(response: str, answer: str) -> float:
    """Score only the final boxed answer, never incidental numbers in CLI text."""
    matches = list(re.finditer(r"\\boxed\s*\{", response))
    if not matches:
        return 0.0
    start = matches[-1].start()
    depth = 1
    end = matches[-1].end()
    while end < len(response) and depth:
        if response[end] == "{":
            depth += 1
        elif response[end] == "}":
            depth -= 1
        end += 1
    if depth:
        return 0.0
    response = response[start:end]
    normalized = unicodedata.normalize("NFKC", response)
    normalized = "".join(
        str(unicodedata.decimal(char)) if char.isdecimal() else char for char in normalized
    )
    return strict_math_reward(normalized, answer)


def _training_claude_math_reward(response: str, answer: str) -> float:
    from math_verify.errors import TimeoutException  # type: ignore[import-untyped]

    try:
        return _claude_math_reward(response, answer)
    except TimeoutException:
        logging.getLogger(__name__).warning(
            "Training math comparison timed out after retry; assigning zero reward: "
            "answer=%r response_prefix=%r",
            answer,
            response[:256],
        )
        return 0.0
    except ValueError as error:
        if str(error) == "Frozen TIR gold answer cannot be parsed":
            raise
        logging.getLogger(__name__).warning(
            "Training answer could not be parsed; assigning zero reward: "
            "error=%r response_prefix=%r",
            error,
            response[:256],
        )
        return 0.0


def _regular_bytes(path: Path) -> bytes:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"Claude Code Harness input is not a regular file: {path}")
    return path.read_bytes()


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _backend_model_digest(root: Path) -> str:
    """Bind the loadable model files without mutable evaluation logs or cache dirs."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Backend model must be a local directory")
    files = sorted(
        path
        for path in root.iterdir()
        if path.is_file()
        and (
            path.name.endswith((".safetensors", ".json", ".jinja", ".py"))
            or path.name in {"merges.txt", "vocab.txt"}
        )
    )
    if not (root / "config.json").is_file() or not any(
        path.suffix == ".safetensors" for path in files
    ):
        raise ValueError("Backend model lacks configuration or weights")
    digest = hashlib.sha256()
    for path in files:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError(f"Backend model contains a symlink or special file: {path}")
        digest.update(path.name.encode() + b"\0" + bytes.fromhex(_digest_file(path)[7:]))
    return "sha256:" + digest.hexdigest()


def _harness_id(spec: AgentHarnessSpec) -> str:
    return (
        "harness_"
        + hashlib.sha256(json_bytes(replace(spec, harness_id="harness_pending"))).hexdigest()
    )


def freeze_claude_harness(
    prompt_file: Path,
    executable: Path,
    backend_model_root: Path,
    destination: Path,
    *,
    model: str,
    timeout_seconds: int = 300,
    max_budget_usd: float = 1.0,
) -> AgentHarnessSpec:
    if destination.exists():
        raise FileExistsError(destination)
    prompt = _regular_bytes(prompt_file)
    validate_prompt(prompt.decode())
    _regular_bytes(executable)
    calculator = Path(calculator_tool.__file__)
    provisional = AgentHarnessSpec(
        "harness_pending",
        digest_bytes(prompt),
        _digest_file(executable),
        digest_bytes(_regular_bytes(calculator)),
        _backend_model_digest(backend_model_root),
        model,
        timeout_seconds,
        max_budget_usd,
    )
    spec = replace(provisional, harness_id=_harness_id(provisional))
    destination.mkdir(parents=True)
    write_bytes(destination / "prompt.txt", prompt, immutable=True)
    write_bytes(destination / "manifest.json", json_bytes(spec), immutable=True)
    return spec


def load_claude_harness(
    root: Path, executable: Path, backend_model_root: Path, calculator: Path | None = None
) -> tuple[AgentHarnessSpec, str]:
    spec = read_record(root / "manifest.json", AgentHarnessSpec)
    prompt = _regular_bytes(root / "prompt.txt")
    validate_prompt(prompt.decode())
    calculator = calculator or Path(calculator_tool.__file__)
    if (
        spec.harness_id != _harness_id(spec)
        or spec.prompt_digest != digest_bytes(prompt)
        or spec.executable_digest != _digest_file(executable)
        or spec.calculator_digest != digest_bytes(_regular_bytes(calculator))
        or spec.backend_model_digest != _backend_model_digest(backend_model_root)
    ):
        raise ValueError("Frozen Claude Code Harness changed")
    return spec, prompt.decode()


class FrozenClaudeWorkflow:
    """Run the frozen Claude Code solver through AReaL's rollout proxy."""

    def __init__(
        self,
        *,
        task_set_root: str,
        harness_root: str,
        claude_cli: str,
        backend_model_root: str,
        task_set_id: str,
        harness_id: str,
    ) -> None:
        root = Path(task_set_root)
        manifest = load_task_set(root)
        spec, template = load_claude_harness(
            Path(harness_root), Path(claude_cli), Path(backend_model_root)
        )
        if manifest.task_set_id != task_set_id or spec.harness_id != harness_id:
            raise ValueError("Frozen Claude rollout inputs changed")
        answers: dict[str, str] = json.loads((root / "private/answers.json").read_bytes())
        self.cases = {
            task.task_id: (task.input["messages"], _gold_answer(answers[task.task_id]))
            for split in (RsiSplit.MODEL_TRAIN, RsiSplit.MODEL_DEV)
            for task in public_tasks(root, split)
        }
        self.spec = spec
        self.template = template
        self.executable = Path(claude_cli)
        self.reward_executor = ProcessPoolExecutor(max_workers=2)

    async def run(self, data: dict[str, Any], **extra_kwargs: Any) -> float:
        task_id = data.get("task_id")
        if not isinstance(task_id, str) or task_id not in self.cases:
            raise ValueError("Claude rollout received an unknown task")
        messages, answer = self.cases[task_id]
        if data.get("messages") != messages or data.get("answer") != answer:
            raise ValueError("Claude rollout task differs from the frozen dataset")
        url, api_key = extra_kwargs.get("base_url"), extra_kwargs.get("api_key")
        if not isinstance(url, str) or not isinstance(api_key, str) or not api_key:
            raise ValueError("AReaL did not provide a session-scoped Claude proxy")
        question = messages[0]["content"]
        try:
            raw = await asyncio.to_thread(
                run_claude,
                self.executable,
                self.spec,
                self.template.format(prompt=question),
                McpTool(
                    "tir_calculator",
                    "calculate",
                    sys.executable,
                    (str(Path(calculator_tool.__file__)),),
                ),
                url,
                api_key=api_key,
            )
        except subprocess.TimeoutExpired:
            return 0.0
        except (ClaudeBudgetLimit, ClaudeOutputLimit):
            return 0.0
        response = final_event(raw).get("result")
        if not isinstance(response, str) or not response.strip():
            return 0.0
        score = asyncio.get_running_loop().run_in_executor(
            self.reward_executor, _training_claude_math_reward, response, answer
        )
        try:
            return await asyncio.wait_for(score, timeout=45)
        except TimeoutError:
            # The independent evaluator surfaces an undecidable comparison;
            # a training rollout needs a bounded zero reward to keep workers alive.
            return 0.0


def evaluate_claude(
    task_root: Path,
    harness_root: Path,
    executable: Path,
    backend_model_root: Path,
    backend_url: str,
    output: Path,
    *,
    split: RsiSplit,
    trial_id: str,
    runner_root: Path,
    python: Path = Path(sys.executable),
    workers: int = 1,
) -> AgentEvaluationReport:
    safe_name(trial_id)
    if not 1 <= workers <= 32:
        raise ValueError("Claude evaluation workers must be between 1 and 32")
    manifest = load_task_set(task_root)
    spec, template = load_claude_harness(harness_root, executable, backend_model_root)
    calculator = Path(calculator_tool.__file__)
    source_digest = source_files_digest(verifier_sources(runner_root))
    answers: Any = json.loads((task_root / "private/answers.json").read_bytes())
    case_root = output.with_suffix(".cases")
    case_root.mkdir(parents=True, exist_ok=True)

    def evaluate_case(task: Task) -> AgentCaseRecord:
        case_path = case_root / f"{task.task_id}.json"
        if case_path.exists():
            case = read_record(case_path, AgentCaseRecord)
            if (
                case.task_id != task.task_id
                or case.harness_id != spec.harness_id
                or case.trial_id != trial_id
                or case.verifier_digest != source_digest
                or digest_bytes(Path(case.transcript_path).read_bytes()) != case.transcript_digest
            ):
                raise ValueError("Cached Claude Code trace changed")
        else:
            question = task.input["messages"][0]["content"]
            if not isinstance(question, str):
                raise ValueError("Invalid public question")
            failure_reason: str | None
            try:
                raw = run_claude(
                    executable,
                    spec,
                    template.format(prompt=question),
                    McpTool("tir_calculator", "calculate", str(python), (str(calculator),)),
                    backend_url,
                )
            except subprocess.TimeoutExpired as error:
                partial = error.stdout or b""
                raw = (
                    partial.rstrip(b"\n")
                    + b"\n"
                    + json_bytes({"type": "local_timeout", "timeout_seconds": spec.timeout_seconds})
                    + b"\n"
                )
                response, input_tokens, output_tokens, cost = "", 0, 0, 0.0
                duration = spec.timeout_seconds * 1000
                failure_reason = "cli_timeout"
            except ClaudeBudgetLimit as error:
                raw = error.transcript
                response, input_tokens, output_tokens, cost = "", 0, 0, 0.0
                duration = 0
                failure_reason = "cli_budget_limit"
            except ClaudeOutputLimit as error:
                raw = (
                    error.transcript.rstrip(b"\n")
                    + b"\n"
                    + json_bytes(
                        {
                            "type": "local_output_limit",
                            "max_output_tokens": CLAUDE_MAX_OUTPUT_TOKENS,
                        }
                    )
                    + b"\n"
                )
                response, input_tokens, output_tokens, cost = "", 0, 0, 0.0
                duration = 0
                failure_reason = "cli_output_limit"
            else:
                try:
                    result = read_result(raw)
                except ValueError:
                    # Preserve malformed CLI output as evidence; it is not a solver answer.
                    response, input_tokens, output_tokens, cost = "", 0, 0, 0.0
                    duration = 0
                    failure_reason = "cli_invalid_result"
                else:
                    response = result.response
                    input_tokens, output_tokens = result.input_tokens, result.output_tokens
                    cost, duration = result.cost_usd, result.duration_ms
                    failure_reason = None
            transcript = case_root / f"{task.task_id}.jsonl"
            write_bytes(transcript, raw, immutable=True)
            if failure_reason:
                score = 0.0
            else:
                score = verifier_pool.submit(
                    _claude_math_reward, response, _gold_answer(answers[task.task_id])
                ).result(timeout=45)
            case = AgentCaseRecord(
                task.task_id,
                spec.harness_id,
                trial_id,
                source_digest,
                response,
                score,
                input_tokens,
                output_tokens,
                cost,
                duration,
                str(transcript.resolve()),
                digest_bytes(raw),
                failure_reason,
            )
            write_bytes(case_path, json_bytes(case), immutable=True)
        return case

    tasks = public_tasks(task_root, split)
    with ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn")) as verifier_pool:
        if workers == 1:
            cases = tuple(map(evaluate_case, tasks))
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                cases = tuple(pool.map(evaluate_case, tasks))
    report = AgentEvaluationReport(
        spec.harness_id,
        manifest.task_set_id,
        split,
        spec.model,
        spec.executable_digest,
        spec.backend_model_digest,
        source_digest,
        trial_id,
        cases,
    )
    write_bytes(output, json_bytes(report), immutable=True)
    return report


@dataclass(frozen=True, slots=True)
class SavedEvaluation:
    report_path: str
    report_digest: str
    source_root: str


@dataclass(frozen=True, slots=True)
class EvaluationReverification:
    original: SavedEvaluation
    original_verifier_digest: str
    verifier_digest: str
    report_digest: str
    cases: int


def reverify_claude_report(
    saved: SavedEvaluation, task_root: Path, output: Path, *, runner_root: Path
) -> AgentEvaluationReport:
    """Recheck authentic saved answers after a source move, without generating new answers."""

    original_path = Path(saved.report_path)
    if digest_bytes(_regular_bytes(original_path)) != saved.report_digest:
        raise ValueError("Saved evaluation report changed")
    original = read_record(original_path, AgentEvaluationReport)
    parent_package = Path(saved.source_root) / "src/areal_rsi"
    if original.verifier_digest != source_files_digest(
        verifier_sources(runner_root, rsi_package=parent_package)
    ):
        raise ValueError("Saved evaluation source bundle changed")
    import areal_rsi

    current_package = Path(areal_rsi.__file__).resolve().parent
    # Preserve the entire source bundle as evidence. Also establish that the
    # actual scorer and transcript parser have not changed across the move.
    for relative in (
        "infra/coordination/evaluation/tir/verifier.py",
        "infra/runtime/claude.py",
    ):
        if _regular_bytes(parent_package / relative) != _regular_bytes(current_package / relative):
            raise ValueError("Saved evaluation grading or parsing policy changed")
    for relative, name in (
        ("infra/runtime/claude_solver.py", "_claude_math_reward"),
        ("infra/coordination/evaluation/assets.py", "_gold_answer"),
    ):
        definitions = []
        for package in (parent_package, current_package):
            text = _regular_bytes(package / relative).decode()
            definition = next(
                (
                    node
                    for node in ast.parse(text).body
                    if isinstance(node, ast.FunctionDef) and node.name == name
                ),
                None,
            )
            if definition is None:
                raise ValueError("Saved evaluation grading policy changed")
            definitions.append(ast.get_source_segment(text, definition))
        if definitions[0] != definitions[1]:
            raise ValueError("Saved evaluation grading policy changed")
    tasks = load_task_set(task_root)
    if (
        original.split is not RsiSplit.MODEL_DEV
        or original.task_set_id != tasks.task_set_id
        or tuple(case.task_id for case in original.cases) != tasks.model_dev.task_ids
    ):
        raise ValueError("Saved evaluation does not match the frozen dev questions")
    answers = decode(dict[str, str], json.loads((task_root / "private/answers.json").read_bytes()))
    source_digest = source_files_digest(verifier_sources(runner_root))
    cases = []
    with ProcessPoolExecutor(max_workers=2, mp_context=get_context("spawn")) as pool:
        for case in original.cases:
            transcript = Path(case.transcript_path)
            raw = _regular_bytes(transcript)
            if (
                digest_bytes(raw) != case.transcript_digest
                or read_record(transcript.with_suffix(".json"), AgentCaseRecord) != case
            ):
                raise ValueError("Saved evaluation case or transcript changed")
            score = 0.0
            if case.failure_reason is None:
                result = read_result(raw)
                if (
                    result.response != case.response
                    or result.input_tokens != case.input_tokens
                    or result.output_tokens != case.output_tokens
                    or result.cost_usd != case.cost_usd
                    or result.duration_ms != case.duration_ms
                ):
                    raise ValueError("Saved evaluation response differs from its transcript")
                score = pool.submit(
                    _claude_math_reward, result.response, _gold_answer(answers[case.task_id])
                ).result(timeout=45)
            if score != case.score:
                raise ValueError("Saved evaluation score could not be reproduced")
            cases.append(replace(case, verifier_digest=source_digest))
    report = replace(original, verifier_digest=source_digest, cases=tuple(cases))
    write_bytes(output, json_bytes(report), immutable=True)
    write_bytes(
        output.with_name(output.stem + "-reverification.json"),
        json_bytes(
            EvaluationReverification(
                saved,
                original.verifier_digest,
                source_digest,
                digest_bytes(json_bytes(report)),
                len(report.cases),
            )
        ),
        immutable=True,
    )
    return report


def propose_claude_harness(
    task_root: Path,
    baseline_path: Path,
    harness_root: Path,
    executable: Path,
    backend_model_root: Path,
    backend_url: str,
    output_root: Path,
    *,
    max_cases: int = 8,
    proposal_index: int = 0,
    python: Path = Path(sys.executable),
) -> AgentHarnessSpec:
    """Ask the solving Claude Code agent to revise its reusable public-task instructions."""
    if not 1 <= max_cases <= 64 or proposal_index < 0 or output_root.exists():
        raise ValueError("Invalid proposal budget or an existing proposal directory")
    manifest = load_task_set(task_root)
    spec, template = load_claude_harness(harness_root, executable, backend_model_root)
    baseline = read_record(baseline_path, AgentEvaluationReport)
    tasks = public_tasks(task_root, RsiSplit.HARNESS_EVOLVE)
    if (
        baseline.split is not RsiSplit.HARNESS_EVOLVE
        or baseline.task_set_id != manifest.task_set_id
        or baseline.harness_id != spec.harness_id
        or tuple(case.task_id for case in baseline.cases) != tuple(task.task_id for task in tasks)
    ):
        raise ValueError("Claude proposal needs matching public failure feedback")
    failures = sorted(
        (case for case in baseline.cases if case.score < 1),
        key=lambda item: (item.score, item.task_id),
    )
    if not failures:
        raise ValueError("Claude proposal needs public failures")
    offset = proposal_index * max_cases % len(failures)
    sampled = (failures + failures)[offset : offset + min(max_cases, len(failures))]
    if {case.task_id for case in sampled} - {task.task_id for task in tasks}:
        raise ValueError("Claude proposal feedback contains an unknown public task")
    missing_box = sum(r"\boxed{" not in case.response for case in sampled)
    wrong_box = len(sampled) - missing_box
    focus = (
        "finish within the output limit with a final boxed answer",
        "check the requested quantity and verify signs or units",
        "use the calculator for exact arithmetic and check its result",
        "write only essential equations before the final boxed result",
    )[proposal_index % 4]
    request = (
        "A guide to improving mathematical problem solving.\n\n"
        "Observed error: The solution rounds intermediate calculations "
        "and gets an inaccurate answer.\n"
        "Advice: Keep intermediate values exact and round only the final answer.\n\n"
        "Observed error: The solution stops before stating its conclusion.\n"
        "Advice: State the requested final answer clearly after completing the calculation.\n\n"
        f"Observed error: {len(sampled)} incorrect answers; "
        f"{missing_box} omitted a boxed final answer and {wrong_box} boxed an incorrect answer.\n"
        f"Review focus: {focus}.\n"
        "Advice:"
    )
    output_root.mkdir(parents=True)
    write_bytes(
        output_root / "request.json",
        json_bytes(
            {
                "baseline_report_digest": digest_bytes(baseline_path.read_bytes()),
                "harness_id": spec.harness_id,
                "proposal_index": proposal_index,
                "proposer_transport": "local_text_completion",
                "model": spec.model,
                "sampling_seed": 1700 + proposal_index,
                "public_failure_feedback": {
                    "sampled_task_ids": [case.task_id for case in sampled],
                    "missing_box": missing_box,
                    "wrong_box": wrong_box,
                },
                "request": request,
            }
        ),
        immutable=True,
    )
    raw = complete_instruction(
        backend_url,
        spec.model,
        request,
        seed=1700 + proposal_index,
        timeout=spec.timeout_seconds,
    )
    write_bytes(output_root / "proposal.response.json", raw, immutable=True)
    response = decode(dict[str, Any], json.loads(raw))
    choices = decode(list[dict[str, Any]], response.get("choices"))
    if (
        response.get("model") != spec.model
        or len(choices) != 1
        or choices[0].get("finish_reason") != "stop"
    ):
        raise ValueError("Proposal completion model, count, or termination differs")
    instructions = decode(str, choices[0].get("text"))
    instructions = " ".join(instructions.split())
    valid = (
        5 <= len(instructions.split()) <= 30
        and not any(character in instructions for character in '{}\\"`')
        and not re.search(r"\b(?:code|python|today|print|example)\b", instructions, re.I)
        and not any(
            len(task.input["messages"][0]["content"]) > 40
            and task.input["messages"][0]["content"] in instructions
            for task in tasks
        )
    )
    write_bytes(
        output_root / "proposal-origin.json",
        json_bytes(
            {
                "origin": "same_model_proposal" if valid else "invalid_model_proposal",
                "instruction": instructions,
                "proposal_response_digest": digest_bytes(raw),
            }
        ),
        immutable=True,
    )
    if not valid:
        raise ValueError("Model proposal is not a valid reusable solver instruction")
    incumbent_prefix = template.split("User: {prompt}", 1)[0].rstrip()
    candidate = (
        incumbent_prefix
        + "\n"
        + instructions
        + "\nUser: {prompt}\nAssistant: Solve the question and end with one final answer "
        "in LaTeX boxed form.\n/no_think\n"
    )
    validate_prompt(candidate)
    prompt_path = output_root / "candidate.txt"
    write_bytes(prompt_path, candidate.encode(), immutable=True)
    return freeze_claude_harness(
        prompt_path,
        executable,
        backend_model_root,
        output_root / "harness",
        model=spec.model,
        timeout_seconds=spec.timeout_seconds,
        max_budget_usd=spec.max_budget_usd,
    )


def search_claude_harnesses(
    task_root: Path,
    baseline_evolve_path: Path,
    baseline_select_path: Path,
    baseline_repeat_path: Path,
    harness_root: Path,
    executable: Path,
    backend_model_root: Path,
    backend_url: str,
    output_root: Path,
    *,
    candidate_count: int,
    runner_root: Path,
    max_cases: int = 8,
    on_no_improvement: Literal["reuse", "stop"] = "reuse",
    workers_per_candidate: int = 1,
    candidate_prompt_files: tuple[Path, ...] = (),
) -> AgentHarnessSearchDecision:
    """Propose and evaluate independent H0 branches concurrently on one task set."""
    if not 1 <= candidate_count <= 16:
        raise ValueError("Candidate count must be between 1 and 16")
    if candidate_prompt_files and len(candidate_prompt_files) != candidate_count:
        raise ValueError("Candidate prompt count differs from the search width")
    incumbent, _ = load_claude_harness(harness_root, executable, backend_model_root)
    evolve = read_record(baseline_evolve_path, AgentEvaluationReport)
    baseline = read_record(baseline_select_path, AgentEvaluationReport)
    repeat = read_record(baseline_repeat_path, AgentEvaluationReport)
    task_set = load_task_set(task_root)
    if (
        evolve.split is not RsiSplit.HARNESS_EVOLVE
        or evolve.harness_id != incumbent.harness_id
        or evolve.task_set_id != task_set.task_set_id
        or baseline.harness_id != incumbent.harness_id
        or baseline.task_set_id != task_set.task_set_id
        or repeat.task_set_id != task_set.task_set_id
    ):
        raise ValueError("Agent search baseline differs from the frozen inputs")
    output_root.mkdir(parents=True, exist_ok=True)

    def branch(index: int) -> AgentEvaluationReport | AgentHarnessAttemptFailure:
        root = output_root / f"candidate-{index:02d}"
        candidate_root = root / "harness"
        if candidate_root.exists():
            load_claude_harness(candidate_root, executable, backend_model_root)
        elif candidate_prompt_files:
            root.mkdir(parents=True)
            freeze_claude_harness(
                candidate_prompt_files[index],
                executable,
                backend_model_root,
                candidate_root,
                model=incumbent.model,
                timeout_seconds=incumbent.timeout_seconds,
                max_budget_usd=incumbent.max_budget_usd,
            )
        else:
            failure_path = root / "failure.json"
            if failure_path.exists():
                return read_record(failure_path, AgentHarnessAttemptFailure)
            try:
                propose_claude_harness(
                    task_root,
                    baseline_evolve_path,
                    harness_root,
                    executable,
                    backend_model_root,
                    backend_url,
                    root,
                    max_cases=max_cases,
                    proposal_index=index,
                )
            except (
                ClaudeBudgetLimit,
                ClaudeOutputLimit,
                subprocess.TimeoutExpired,
                ValueError,
            ) as error:
                root.mkdir(parents=True, exist_ok=True)
                if isinstance(error, (ClaudeBudgetLimit, ClaudeOutputLimit)):
                    write_bytes(
                        root / "proposal.trajectory.jsonl", error.transcript, immutable=True
                    )
                failure = AgentHarnessAttemptFailure(index, str(error))
                write_bytes(failure_path, json_bytes(failure), immutable=True)
                return failure
        return evaluate_claude(
            task_root,
            candidate_root,
            executable,
            backend_model_root,
            backend_url,
            root / "selection.json",
            split=RsiSplit.HARNESS_SELECT,
            trial_id=f"candidate_{index:02d}_select",
            runner_root=runner_root,
            workers=workers_per_candidate,
        )

    with ThreadPoolExecutor(max_workers=candidate_count) as pool:
        results = tuple(pool.map(branch, range(candidate_count)))
    reports = tuple(result for result in results if isinstance(result, AgentEvaluationReport))
    failures = tuple(result for result in results if isinstance(result, AgentHarnessAttemptFailure))
    decision = replace(
        select_agent_harnesses(
            baseline,
            repeat,
            reports,
            on_no_improvement=on_no_improvement,
        ),
        failures=failures,
    )
    write_bytes(output_root / "decision.json", json_bytes(decision), immutable=True)
    return decision


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze or evaluate a Claude Code TIR Harness")
    actions = parser.add_subparsers(dest="action", required=True)
    freeze = actions.add_parser("freeze")
    freeze.add_argument("--prompt", type=Path, required=True)
    freeze.add_argument("--claude-cli", type=Path, required=True)
    freeze.add_argument("--backend-model-root", type=Path, required=True)
    freeze.add_argument("--output", type=Path, required=True)
    freeze.add_argument("--model", required=True)
    freeze.add_argument("--timeout-seconds", type=int, default=300)
    freeze.add_argument("--max-budget-usd", type=float, default=1.0)
    run = actions.add_parser("evaluate")
    run.add_argument("--task-set-root", type=Path, required=True)
    run.add_argument("--harness-root", type=Path, required=True)
    run.add_argument("--claude-cli", type=Path, required=True)
    run.add_argument("--backend-model-root", type=Path, required=True)
    run.add_argument("--backend-url", required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--split", type=RsiSplit, required=True)
    run.add_argument("--trial-id", required=True)
    run.add_argument("--runner-root", type=Path, required=True)
    run.add_argument("--workers", type=int, default=1)
    propose = actions.add_parser("propose")
    propose.add_argument("--task-set-root", type=Path, required=True)
    propose.add_argument("--baseline-report", type=Path, required=True)
    propose.add_argument("--harness-root", type=Path, required=True)
    propose.add_argument("--claude-cli", type=Path, required=True)
    propose.add_argument("--backend-model-root", type=Path, required=True)
    propose.add_argument("--backend-url", required=True)
    propose.add_argument("--output-root", type=Path, required=True)
    propose.add_argument("--max-cases", type=int, default=8)
    propose.add_argument("--proposal-index", type=int, default=0)
    search = actions.add_parser("search")
    search.add_argument("--task-set-root", type=Path, required=True)
    search.add_argument("--baseline-evolve", type=Path, required=True)
    search.add_argument("--baseline-select", type=Path, required=True)
    search.add_argument("--baseline-repeat", type=Path, required=True)
    search.add_argument("--harness-root", type=Path, required=True)
    search.add_argument("--claude-cli", type=Path, required=True)
    search.add_argument("--backend-model-root", type=Path, required=True)
    search.add_argument("--backend-url", required=True)
    search.add_argument("--output-root", type=Path, required=True)
    search.add_argument("--runner-root", type=Path, required=True)
    search.add_argument("--candidate-count", type=int, required=True)
    search.add_argument("--max-cases", type=int, default=8)
    search.add_argument("--on-no-improvement", choices=("reuse", "stop"), default="reuse")
    search.add_argument("--workers-per-candidate", type=int, default=1)
    search.add_argument("--candidate-prompt", type=Path, action="append", default=[])
    args = parser.parse_args()
    if args.action == "freeze":
        spec = freeze_claude_harness(
            args.prompt,
            args.claude_cli,
            args.backend_model_root,
            args.output,
            model=args.model,
            timeout_seconds=args.timeout_seconds,
            max_budget_usd=args.max_budget_usd,
        )
        print(json_bytes(spec).decode())
    elif args.action == "propose":
        proposal = propose_claude_harness(
            args.task_set_root,
            args.baseline_report,
            args.harness_root,
            args.claude_cli,
            args.backend_model_root,
            args.backend_url,
            args.output_root,
            max_cases=args.max_cases,
            proposal_index=args.proposal_index,
        )
        print(json_bytes(proposal).decode())
    elif args.action == "search":
        decision = search_claude_harnesses(
            args.task_set_root,
            args.baseline_evolve,
            args.baseline_select,
            args.baseline_repeat,
            args.harness_root,
            args.claude_cli,
            args.backend_model_root,
            args.backend_url,
            args.output_root,
            candidate_count=args.candidate_count,
            runner_root=args.runner_root,
            max_cases=args.max_cases,
            on_no_improvement=args.on_no_improvement,
            workers_per_candidate=args.workers_per_candidate,
            candidate_prompt_files=tuple(args.candidate_prompt),
        )
        print(
            json.dumps(
                {
                    "selected_harness_id": decision.selected_harness_id,
                    "continue_to_training": decision.continue_to_training,
                    "candidate_scores": [choice.correct for choice in decision.candidates],
                }
            )
        )
    else:
        evaluation = evaluate_claude(
            args.task_set_root,
            args.harness_root,
            args.claude_cli,
            args.backend_model_root,
            args.backend_url,
            args.output,
            split=args.split,
            trial_id=args.trial_id,
            runner_root=args.runner_root,
            workers=args.workers,
        )
        print(json.dumps({"score": evaluation.score, "cases": len(evaluation.cases)}))


if __name__ == "__main__":
    main()
