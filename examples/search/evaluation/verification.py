"""Verify existing solver/judge outputs and recover summaries without new inference."""

from __future__ import annotations

import ast
import csv
import hashlib
import json
import math
import re
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any

from examples.search.evaluation.analysis import read_official_results
from examples.search.evaluation.artifacts import (
    ContextBudgetExhaustion,
    EvaluationRun,
    FinalEvalConfig,
    FinalEvaluationSummary,
    SolverVerification,
    SummaryRecoveryReceipt,
    ToolArgumentFailure,
    artifact,
    digest,
)


def context_budget_exhaustion(path: Path, record: dict[str, Any]) -> ContextBudgetExhaustion:
    """Accept only an exact context-budget refusal with unchanged original evidence.

    The Qwen client saves an incomplete run before the batch worker overwrites
    its status for every exception. Transport faults and answers are excluded.
    This does not truncate a prompt, retry generation, or modify any artifact.
    """
    failure = "Unrecoverable evaluation error; preserve it as a failed run"
    error = record.get("error")
    if record.get("status") != "error" or not isinstance(error, str):
        raise ValueError(failure)
    prefix = "qwen_agent.llm.base.ModelServiceError: Error code: 400 - "
    last_line = error.splitlines()[-1] if error else ""
    if not last_line.startswith(prefix):
        raise ValueError(failure)
    try:
        payload = ast.literal_eval(last_line[len(prefix) :])
    except (ValueError, SyntaxError):
        raise ValueError(failure) from None
    detail = payload.get("error") if isinstance(payload, dict) else None
    if (
        not isinstance(detail, dict)
        or detail.get("type") != "BadRequestError"
        or detail.get("code") != 400
        or detail.get("param") != "input_tokens"
        or not isinstance(detail.get("message"), str)
    ):
        raise ValueError(failure)
    match = re.fullmatch(
        r"You passed (\d+) input tokens and requested (\d+) output tokens\. "
        r"However, the model's context length is only (\d+) tokens, resulting in "
        r"a maximum input length of (\d+) tokens\. Please reduce the length of "
        r"the input prompt\. \(parameter=input_tokens, value=(\d+)\)",
        detail["message"],
    )
    if match is None:
        raise ValueError(failure)
    inputs, outputs, context, maximum, value = map(int, match.groups())
    if (
        context != 131072
        or outputs != 10000
        or maximum != context - outputs
        or value != inputs
        or inputs <= maximum
        or record.get("metadata", {}).get("max_output_tokens") != outputs
    ):
        raise ValueError(failure)
    results = record.get("result")
    if not isinstance(results, list) or any(
        item.get("type") == "output_text" and str(item.get("output", "")).strip()
        for item in results
    ):
        raise ValueError(failure)
    query_id = str(record["query_id"])
    if not query_id or Path(query_id).name != query_id or query_id in {".", ".."}:
        raise ValueError(failure)
    raw = path.parent.parent / (path.parent.name + "-raw") / query_id
    originals = list(raw.glob("run_*.json"))
    if len(originals) != 1:
        raise ValueError(failure)
    original = json.loads(originals[0].read_text())
    messages_path = raw / "messages.json"
    messages = json.loads(messages_path.read_text())
    response = messages.get("response")
    if (
        original.get("status") != "incomplete"
        or str(original.get("query_id")) != query_id
        or any(
            original.get(key) != record.get(key)
            for key in ("result", "retrieved_docids", "tool_call_counts")
        )
        or original.get("metadata", {}).get("max_output_tokens") != outputs
        or messages.get("error") != error
        or messages.get("request_config", {}).get("max_tokens") != outputs
        or not isinstance(response, list)
        or not response
        or response[-1].get("role") != "function"
    ):
        raise ValueError(failure)
    return ContextBudgetExhaustion(
        query_id, inputs, outputs, context, artifact(originals[0]), artifact(messages_path)
    )


def tool_argument_failure(
    config: FinalEvalConfig, path: Path, record: dict[str, Any], result_index: int
) -> ToolArgumentFailure:
    """Prove a local malformed model action, rejecting unknown service error text."""
    failure = "Unverified tool error; retain infrastructure failure status"
    item = record["result"][result_index]
    arguments, observation = item.get("arguments"), item.get("output")
    if (
        item.get("tool_name") != "search"
        or not isinstance(arguments, str)
        or not isinstance(observation, str)
    ):
        raise ValueError(failure)
    header = "An error occurred when calling tool `search-server-search`:\n"
    manager = str(Path(config.qwen_overlay) / "qwen_agent/tools/mcp_manager.py")
    if not observation.startswith(header) or manager not in observation:
        raise ValueError(failure)
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError as error:
        if (
            not observation.startswith(header + f"JSONDecodeError: {error}\nTraceback:\n")
            or "tool_args = json.loads(params)" not in observation
        ):
            raise ValueError(failure) from None
        kind = "model-arguments-invalid-json-before-dispatch"
    else:
        if (
            not isinstance(parsed, str)
            or not observation.startswith(
                header
                + "ValidationError: 1 validation error for CallToolRequestParams\narguments\n"
            )
            or "type=dict_type" not in observation
            or "input_type=str]" not in observation
            or "/mcp/client/session.py" not in observation
        ):
            raise ValueError(failure)
        kind = "model-arguments-string-instead-of-object-before-dispatch"
    query_id = str(record["query_id"])
    if not query_id or Path(query_id).name != query_id or query_id in {".", ".."}:
        raise ValueError(failure)
    raw = path.parent.parent / (path.parent.name + "-raw") / query_id
    originals = list(raw.glob("run_*.json"))
    if len(originals) != 1:
        raise ValueError(failure)
    original = json.loads(originals[0].read_text())
    messages = raw / "messages.json"
    raw_messages = json.loads(messages.read_text())
    if (
        any(
            original.get(key) != record.get(key)
            for key in ("query_id", "status", "result", "retrieved_docids", "tool_call_counts")
        )
        or raw_messages.get("error") is not None
        or not any(
            m.get("role") == "function" and m.get("content") == observation
            for m in raw_messages.get("response", [])
        )
    ):
        raise ValueError(failure)
    return ToolArgumentFailure(
        query_id,
        result_index,
        kind,
        hashlib.sha256(arguments.encode()).hexdigest(),
        hashlib.sha256(observation.encode()).hexdigest(),
        artifact(originals[0]),
        artifact(messages),
    )


def verify_solver_outputs(config: FinalEvalConfig) -> SolverVerification:
    """Check all original runs before judge-only continuation; never regenerate."""
    with (Path(config.data_root) / "datasets/queries.tsv").open() as query_source:
        rows = list(csv.reader(query_source, delimiter="\t"))
    expected = {row[0]: hashlib.sha256(row[1].encode()).hexdigest() for row in rows}
    if len(expected) != 830 or len(rows) != 830:
        raise ValueError("Solver verification requires all830 unique original queries")
    source = Path(config.output)
    directory = source / "runs/qwen3-embedding-8b" / source.name
    identifiers = []
    completed = incomplete = 0
    budgets = []
    tool_failures = []
    artifacts = []
    protocol = digest(Path(config.benchmark_repository) / "local/react_reproduction.json")
    for path in sorted(directory.glob("run_*.json")):
        record = json.loads(path.read_text())
        query_id = str(record["query_id"])
        metadata = record["metadata"]
        if (
            query_id not in expected
            or metadata.get("query_sha256") != expected[query_id]
            or metadata.get("protocol_sha256") != protocol
            or metadata.get("seed") != config.seed
        ):
            raise ValueError("Solver query/seed/protocol differs from frozen source")
        identifiers.append(query_id)
        if record["status"] == "error":
            budgets.append(context_budget_exhaustion(path, record))
        elif record["status"] == "completed":
            completed += 1
        elif record["status"] == "incomplete":
            incomplete += 1
        else:
            raise ValueError("Unknown solver status")
        docids: set[str] = set()
        for index, item in enumerate(record["result"]):
            if item.get("type") != "tool_call" or not item.get("output"):
                continue
            value = item["output"]
            try:
                decoded = json.loads(value) if isinstance(value, str) else value
            except json.JSONDecodeError:
                tool_failures.append(tool_argument_failure(config, path, record, index))
                continue
            if not isinstance(decoded, list) or any(
                not isinstance(d, dict) or "docid" not in d for d in decoded
            ):
                raise ValueError("Unverified tool result schema; reject service failures")
            docids.update(str(d["docid"]) for d in decoded)
        if set(record["retrieved_docids"]) != docids:
            raise ValueError("Original retrieved document IDs differ from successful tool returns")
        artifacts.append(artifact(path))
    if len(identifiers) != 830 or set(identifiers) != set(expected):
        raise ValueError("Solver coverage missing or duplicated")
    return SolverVerification(
        str(source),
        830,
        completed,
        incomplete,
        tuple(budgets),
        tuple(tool_failures),
        tuple(artifacts),
    )


def summarize_evaluation(
    config: FinalEvalConfig,
    *,
    elapsed_seconds: float,
    allow_context_budget_exhaustion: bool = False,
    recovery_receipt: Path | None = None,
) -> tuple[FinalEvaluationSummary, tuple[EvaluationRun, ...]]:
    """Summarize complete original solver/judge files with strict failure accounting."""
    with (Path(config.data_root) / "datasets/queries.tsv").open() as source:
        identifiers = [row[0] for row in csv.reader(source, delimiter="\t")]
    expected = set(identifiers)
    if len(expected) != 830 or len(identifiers) != 830:
        raise ValueError("Final evaluation requires all 830 unique queries")
    output = Path(config.output)
    directory = output / "runs/qwen3-embedding-8b" / output.name
    judged = output / "evals/qwen3-embedding-8b" / output.name
    correctness = read_official_results(judged, expected)
    official_summary = json.loads((judged / "evaluation_summary.json").read_text())
    runs = []
    for path in sorted(directory.glob("run_*.json")):
        record = json.loads(path.read_text())
        query_id = str(record["query_id"])
        if record["status"] == "error":
            if not allow_context_budget_exhaustion:
                raise ValueError(
                    "Missing runs or infrastructure failures invalidate final evaluation"
                )
            exhaustion = context_budget_exhaustion(path, record)
            # A budget-exhausted case stays wrong in the original official score.
            judgment = json.loads((judged / f"{path.stem}_eval.json").read_text())
            if (
                str(judgment["query_id"]) != query_id
                or judgment["is_completed"]
                or correctness[query_id]
            ):
                raise ValueError(
                    "A context-budget failure cannot become a completed/correct answer"
                )
        elif record["status"] in {"completed", "incomplete"}:
            exhaustion = None
        else:
            raise ValueError("Unknown evaluation status")
        calls = record["tool_call_counts"].get("search", 0)
        duration = record["metadata"]["elapsed_seconds"]
        if type(calls) is not int or calls < 0 or not math.isfinite(duration) or duration < 0:
            raise ValueError("Invalid run cost/latency")
        runs.append(EvaluationRun(query_id, calls, duration, artifact(path), exhaustion))
    if len(runs) != 830 or {row.query_id for row in runs} != expected:
        raise ValueError("Missing runs or duplicate query IDs invalidate final evaluation")
    summary = FinalEvaluationSummary(
        questions=830,
        correct=sum(correctness.values()),
        accuracy=sum(correctness.values()) / 830,
        official_metrics={
            key: value for key, value in official_summary.items() if key != "per_query_metrics"
        },
        mean_search_calls=sum(row.search_calls for row in runs) / 830,
        mean_query_seconds=sum(row.elapsed_seconds for row in runs) / 830,
        elapsed_seconds=elapsed_seconds,
        official_results=str(judged),
        status="evaluated; full goal completion requires paired comparisons and all other gates",
        context_budget_exhaustions=sum(row.context_budget_exhaustion is not None for row in runs),
        recovery_receipt=str(recovery_receipt) if recovery_receipt else None,
    )
    return summary, tuple(runs)


def recover_summary(config_path: Path, output: Path) -> Path:
    """CPU-only, separately recorded recovery after solver and official judge finish."""
    from areal_rsi.infra.storage.artifacts import write_record

    config = FinalEvalConfig(**json.loads(config_path.read_text()))
    source = Path(config.output).resolve()
    output = output.resolve()
    if output.is_relative_to(source) or output.exists() or (source / "result.json").exists():
        raise ValueError("Recovery requires a new summary outside the original output directory")
    environment_path = source / "environment.json"
    environment = json.loads(environment_path.read_text())
    if (
        environment.get("asset_hashes_verified") is not True
        or environment.get("historical_source_hashes_verified") is not True
        or environment.get("asset_lock_sha256") != config.asset_lock_sha256
        or digest(Path(config.asset_lock)) != config.asset_lock_sha256
        or digest(Path(config.checkpoint_manifest)) != config.checkpoint_manifest_sha256
    ):
        raise ValueError("Recovery source/configuration provenance is not verified")
    job = str(environment["slurm_job_id"])
    observed = (
        subprocess.check_output(
            ["sacct", "-X", "-n", "-P", "-j", job, "--format=JobIDRaw,State,ElapsedRaw"],
            text=True,
            timeout=30,
        )
        .strip()
        .splitlines()
    )
    if len(observed) != 1:
        raise ValueError("Re-poll the actual source job; no terminal observation")
    job_id, state, elapsed, *_ = observed[0].split("|")
    if job_id != job or state.split()[0] not in {"FAILED", "COMPLETED"}:
        raise ValueError("Source job must be terminal before recovery; never change live artifacts")
    cost_path = source / "solver-cost.json"
    cost = json.loads(cost_path.read_text())
    if any(
        type(cost.get(key)) is not int or cost[key] <= 0
        for key in ("input_tokens", "output_tokens")
    ):
        raise ValueError("Full solver token accounting is required")
    if not math.isfinite(cost["inference_wall_seconds"]) or cost["inference_wall_seconds"] <= 0:
        raise ValueError("Full solver wall time is required")
    receipt_path = output.with_suffix(".recovery.json")
    summary, runs = summarize_evaluation(
        config,
        elapsed_seconds=float(elapsed),
        allow_context_budget_exhaustion=True,
        recovery_receipt=receipt_path,
    )
    exhaustions = tuple(
        row.context_budget_exhaustion for row in runs if row.context_budget_exhaustion
    )
    if not exhaustions:
        raise ValueError("No strictly verified context-budget error requires recovery")
    judged = Path(summary.official_results)
    paths = {
        config_path,
        environment_path,
        cost_path,
        Path(config.asset_lock),
        Path(config.checkpoint_manifest),
        Path(config.data_root) / "datasets/queries.tsv",
        judged / "evaluation_summary.json",
        *(Path(row.source.path) for row in runs),
        *judged.glob("*_eval.json"),
        *(Path(item.original_record.path) for item in exhaustions),
        *(Path(item.raw_messages.path) for item in exhaustions),
    }
    receipt = SummaryRecoveryReceipt(
        policy="context-budget-exhaustion-v1",
        source_directory=str(source),
        source_job_id=job,
        source_job_state=state.split()[0],
        source_job_elapsed_seconds=int(elapsed),
        recovery_source_sha256=digest(Path(__file__)),
        inputs=tuple(artifact(path) for path in sorted(paths)),
        context_budget_exhaustions=exhaustions,
        scope="Original solver/judge files remain unchanged. Only exact HTTP400 context-budget "
        "refusals with original incomplete/no-final-answer records are accepted as budget "
        "exhaustion, remain wrong in all830, and are never regenerated. Other failures reject "
        "recovery. elapsed_seconds is actual source Slurm allocation time including startup/judge.",
    )
    if receipt_path.exists() and json.loads(receipt_path.read_text()) != json.loads(
        json.dumps(asdict(receipt))
    ):
        raise ValueError("Existing interrupted recovery receipt has different inputs")
    write_record(receipt_path, receipt)
    write_record(output, summary)
    return output
