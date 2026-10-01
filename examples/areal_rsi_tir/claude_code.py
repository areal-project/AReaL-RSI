"""Evaluate a frozen Claude Code solving Harness on public ToRL questions."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from areal_rsi.api.control import json_bytes, read_record, safe_name
from areal_rsi.api.records import (
    AgentCaseRecord,
    AgentEvaluationReport,
    AgentHarnessSpec,
    RsiSplit,
)
from areal_rsi.infra.storage.files import write_bytes
from areal_rsi.scripts.jobs import digest_bytes
from areal_rsi_tir.assets import _gold_answer, source_files_digest, tir_verifier_files
from areal_rsi_tir.harness import validate_tir_prompt
from areal_rsi_tir.tasks import load_tir_task_set, public_tir_tasks
from areal_rsi_tir.verifier import strict_math_reward


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


def _harness_id(spec: AgentHarnessSpec) -> str:
    return (
        "harness_"
        + hashlib.sha256(json_bytes(replace(spec, harness_id="harness_pending"))).hexdigest()
    )


def freeze_claude_harness(
    prompt_file: Path,
    executable: Path,
    destination: Path,
    *,
    model: str,
    timeout_seconds: int = 300,
    max_budget_usd: float = 1.0,
) -> AgentHarnessSpec:
    if destination.exists():
        raise FileExistsError(destination)
    prompt = _regular_bytes(prompt_file)
    validate_tir_prompt(prompt.decode())
    _regular_bytes(executable)
    calculator = Path(__file__).with_name("claude_calculator.py")
    provisional = AgentHarnessSpec(
        "harness_pending",
        digest_bytes(prompt),
        _digest_file(executable),
        digest_bytes(_regular_bytes(calculator)),
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
    root: Path, executable: Path, calculator: Path | None = None
) -> tuple[AgentHarnessSpec, str]:
    spec = read_record(root / "manifest.json", AgentHarnessSpec)
    prompt = _regular_bytes(root / "prompt.txt")
    validate_tir_prompt(prompt.decode())
    calculator = calculator or Path(__file__).with_name("claude_calculator.py")
    if (
        spec.harness_id != _harness_id(spec)
        or spec.prompt_digest != digest_bytes(prompt)
        or spec.executable_digest != _digest_file(executable)
        or spec.calculator_digest != digest_bytes(_regular_bytes(calculator))
    ):
        raise ValueError("Frozen Claude Code Harness changed")
    return spec, prompt.decode()


def _result(raw: bytes) -> tuple[str, int, int, float, int]:
    events = [json.loads(line) for line in raw.splitlines() if line.strip()]
    final = [event for event in events if isinstance(event, dict) and event.get("type") == "result"]
    if len(final) != 1 or final[0].get("is_error") is not False:
        raise ValueError("Claude Code did not return one successful final result")
    event = final[0]
    usage = event.get("usage")
    if not isinstance(usage, dict):
        raise ValueError("Claude Code omitted token usage")
    response = event.get("result")
    input_tokens, output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
    cost, duration = event.get("total_cost_usd"), event.get("duration_ms")
    if (
        not isinstance(response, str)
        or not response.strip()
        or type(input_tokens) is not int
        or type(output_tokens) is not int
        or not isinstance(cost, (float, int))
        or isinstance(cost, bool)
        or type(duration) is not int
    ):
        raise ValueError("Claude Code returned incomplete result metadata")
    return response, input_tokens, output_tokens, float(cost), duration


def _call(
    executable: Path,
    spec: AgentHarnessSpec,
    prompt: str,
    calculator: Path,
    python: Path,
) -> bytes:
    with tempfile.TemporaryDirectory(prefix="tir-claude-case-") as temporary:
        work = Path(temporary)
        mcp = work / "mcp.json"
        mcp.write_bytes(
            json_bytes(
                {
                    "mcpServers": {
                        "tir_calculator": {
                            "command": str(python),
                            "args": [str(calculator)],
                        }
                    }
                }
            )
        )
        command = [
            str(executable),
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--no-session-persistence",
            "--no-chrome",
            "--strict-mcp-config",
            "--setting-sources",
            "",
            "--mcp-config",
            str(mcp),
            "--permission-mode",
            "dontAsk",
            "--tools",
            "",
            "--allowedTools",
            "mcp__tir_calculator__calculate",
            "--model",
            spec.model,
            "--max-budget-usd",
            str(spec.max_budget_usd),
            prompt,
        ]
        completed = subprocess.run(
            command,
            cwd=work,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=spec.timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"Claude Code exited {completed.returncode}: "
                f"{completed.stderr.decode(errors='replace')[:1000]}"
            )
        return completed.stdout


def evaluate_claude(
    task_root: Path,
    harness_root: Path,
    executable: Path,
    output: Path,
    *,
    split: RsiSplit,
    trial_id: str,
    runner_root: Path,
    python: Path = Path(sys.executable),
) -> AgentEvaluationReport:
    safe_name(trial_id)
    manifest = load_tir_task_set(task_root)
    spec, template = load_claude_harness(harness_root, executable)
    calculator = Path(__file__).with_name("claude_calculator.py")
    source_digest = source_files_digest(tir_verifier_files(runner_root))
    answers: Any = json.loads((task_root / "private/answers.json").read_bytes())
    cases: list[AgentCaseRecord] = []
    case_root = output.with_suffix(".cases")
    case_root.mkdir(parents=True, exist_ok=True)
    for task in public_tir_tasks(task_root, split):
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
            raw = _call(executable, spec, template.format(prompt=question), calculator, python)
            response, input_tokens, output_tokens, cost, duration = _result(raw)
            transcript = case_root / f"{task.task_id}.jsonl"
            write_bytes(transcript, raw, immutable=True)
            score = strict_math_reward(response, _gold_answer(answers[task.task_id]))
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
            )
            write_bytes(case_path, json_bytes(case), immutable=True)
        cases.append(case)
    report = AgentEvaluationReport(
        spec.harness_id,
        manifest.task_set_id,
        split,
        spec.model,
        spec.executable_digest,
        source_digest,
        trial_id,
        tuple(cases),
    )
    write_bytes(output, json_bytes(report), immutable=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze or evaluate a Claude Code TIR Harness")
    actions = parser.add_subparsers(dest="action", required=True)
    freeze = actions.add_parser("freeze")
    freeze.add_argument("--prompt", type=Path, required=True)
    freeze.add_argument("--claude-cli", type=Path, required=True)
    freeze.add_argument("--output", type=Path, required=True)
    freeze.add_argument("--model", required=True)
    freeze.add_argument("--timeout-seconds", type=int, default=300)
    freeze.add_argument("--max-budget-usd", type=float, default=1.0)
    run = actions.add_parser("evaluate")
    run.add_argument("--task-set-root", type=Path, required=True)
    run.add_argument("--harness-root", type=Path, required=True)
    run.add_argument("--claude-cli", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--split", type=RsiSplit, required=True)
    run.add_argument("--trial-id", required=True)
    run.add_argument("--runner-root", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "freeze":
        spec = freeze_claude_harness(
            args.prompt,
            args.claude_cli,
            args.output,
            model=args.model,
            timeout_seconds=args.timeout_seconds,
            max_budget_usd=args.max_budget_usd,
        )
        print(json_bytes(spec).decode())
    else:
        result = evaluate_claude(
            args.task_set_root,
            args.harness_root,
            args.claude_cli,
            args.output,
            split=args.split,
            trial_id=args.trial_id,
            runner_root=args.runner_root,
        )
        print(json.dumps({"score": result.score, "cases": len(result.cases)}))


if __name__ == "__main__":
    main()
