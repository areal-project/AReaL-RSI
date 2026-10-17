"""Run frozen original BrowseComp-Plus H0 and its unmodified official judge."""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import math
import os
import re
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from examples.search.analysis import read_official_results


@dataclass(frozen=True, slots=True)
class FinalEvalConfig:
    benchmark_repository: str
    historical_environment: str
    data_root: str
    model: str
    base_model: str
    embedding_model: str
    judge_model: str
    checkpoint_manifest: str
    checkpoint_manifest_sha256: str
    asset_lock: str
    asset_lock_sha256: str
    data_python: str
    vllm_python: str
    qwen_overlay: str
    output: str
    seed: int = 42


@dataclass(frozen=True, slots=True)
class TokenCounters:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class CheckpointExportReceipt:
    source_manifest: str
    source_sha256: str
    export_manifest: str
    export_sha256: str
    unchanged_weight_files: tuple[str, ...]
    added_legacy_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvaluationArtifact:
    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class ContextBudgetExhaustion:
    query_id: str
    input_tokens: int
    requested_output_tokens: int
    context_tokens: int
    original_record: EvaluationArtifact
    raw_messages: EvaluationArtifact


@dataclass(frozen=True, slots=True)
class EvaluationRun:
    query_id: str
    search_calls: int
    elapsed_seconds: float
    source: EvaluationArtifact
    context_budget_exhaustion: ContextBudgetExhaustion | None = None


@dataclass(frozen=True, slots=True)
class FinalEvaluationSummary:
    questions: int
    correct: int
    accuracy: float
    official_metrics: dict[str, Any]
    mean_search_calls: float
    mean_query_seconds: float
    elapsed_seconds: float
    official_results: str
    status: str
    context_budget_exhaustions: int = 0
    recovery_receipt: str | None = None


@dataclass(frozen=True, slots=True)
class SummaryRecoveryReceipt:
    policy: str
    source_directory: str
    source_job_id: str
    source_job_state: str
    source_job_elapsed_seconds: int
    recovery_source_sha256: str
    inputs: tuple[EvaluationArtifact, ...]
    context_budget_exhaustions: tuple[ContextBudgetExhaustion, ...]
    scope: str


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def artifact(path: Path) -> EvaluationArtifact:
    return EvaluationArtifact(str(path.resolve()), digest(path))


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
    from areal_rsi.infra.artifacts import write_record

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


def export_checkpoint(source_manifest: Path, output: Path, *, source_sha256: str) -> Path:
    """Export HF5 Qwen3 metadata for HF4 inference without changing weight bytes.

    HF5 moves RoPE theta into rope_parameters. HF4 otherwise silently uses
    Qwen3's default theta, which differs from the trained/base model's theta.
    Keep both representations, and retain the original checkpoint unchanged.
    """
    from areal_rsi.api.records import DirectorySnapshot
    from areal_rsi.infra.artifacts import read_record, snapshot_directory, write_record

    def verify(snapshot: DirectorySnapshot) -> None:
        for entry in snapshot.files:
            relative = Path(entry.path)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Checkpoint manifest requires relative file paths")
            path = Path(snapshot.root) / relative
            if path.stat().st_size != entry.size or digest(path) != entry.sha256:
                raise ValueError(f"Checkpoint file changed: {entry.path}")

    if digest(source_manifest) != source_sha256:
        raise ValueError("Selected checkpoint manifest changed")
    source = read_record(source_manifest, DirectorySnapshot)
    verify(source)
    root = Path(source.root).resolve(strict=True)
    original = json.loads((root / "config.json").read_text())
    if original.get("model_type") != "qwen3":
        raise ValueError("This export supports the Qwen3 checkpoint format")
    effective = dict(original)
    parameters = original.get("rope_parameters")
    if parameters is not None:
        if not isinstance(parameters, dict) or "rope_theta" not in parameters:
            raise ValueError("Expected flat Qwen3 RoPE parameters with an explicit theta")
        theta = parameters["rope_theta"]
        if type(theta) not in {int, float} or not math.isfinite(theta) or theta <= 0:
            raise ValueError("Invalid saved RoPE theta")
        scaling = {key: value for key, value in parameters.items() if key != "rope_theta"}
        for key, value in (("rope_theta", theta), ("rope_scaling", scaling)):
            if original.get(key) is not None and original[key] != value:
                raise ValueError("Conflicting modern and legacy RoPE metadata")
            effective[key] = value
    if effective.get("rope_theta") is None:
        raise ValueError("Cannot infer a missing RoPE theta from a library default")
    dtype = original.get("dtype") or original.get("torch_dtype")
    if dtype not in {"bfloat16", "float16", "float32"}:
        raise ValueError("Checkpoint requires an explicit supported dtype")
    if original.get("torch_dtype") not in {None, dtype}:
        raise ValueError("Conflicting modern and legacy dtype metadata")
    effective["torch_dtype"] = dtype
    weights = {
        entry.path: entry.sha256 for entry in source.files if entry.path.endswith(".safetensors")
    }
    if not weights:
        raise ValueError("Checkpoint manifest has no model weights")
    output = output.resolve()
    if output.is_relative_to(root):
        raise ValueError("Inference export must be outside the original checkpoint")
    manifest = output.with_suffix(".manifest.json")
    receipt_path = output.with_suffix(".export.json")
    if output.exists():
        receipt = read_record(receipt_path, CheckpointExportReceipt)
        if receipt.source_sha256 != source_sha256 or digest(manifest) != receipt.export_sha256:
            raise ValueError("Existing inference export has different content")
        verify(read_record(manifest, DirectorySnapshot))
        if json.loads((output / "config.json").read_text()) != effective:
            raise ValueError("Existing inference export has different model metadata")
        return manifest
    if manifest.exists() or receipt_path.exists():
        raise ValueError("Inference export records already exist")
    output.mkdir(parents=True)
    for entry in source.files:
        target = output / entry.path
        target.parent.mkdir(parents=True, exist_ok=True)
        if entry.path == "config.json":
            target.write_text(json.dumps(effective, indent=2) + "\n")
        elif entry.path in weights:
            try:
                os.link(root / entry.path, target)
            except OSError:
                shutil.copy2(root / entry.path, target)
        else:
            shutil.copy2(root / entry.path, target)
    reference = snapshot_directory(output, manifest)
    exported = read_record(manifest, DirectorySnapshot)
    exported_weights = {
        entry.path: entry.sha256 for entry in exported.files if entry.path in weights
    }
    if exported_weights != weights:
        raise ValueError("Inference export changed model weights")
    write_record(
        receipt_path,
        CheckpointExportReceipt(
            str(source_manifest.resolve()),
            source_sha256,
            str(manifest),
            reference.digest,
            tuple(sorted(weights)),
            tuple(key for key in effective if effective.get(key) != original.get(key)),
        ),
    )
    return manifest


def counters(port: int) -> TokenCounters:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=10) as response:
        payload = response.read().decode()
    totals = []
    for name in ("prompt_tokens", "generation_tokens"):
        matches = re.findall(
            rf"^vllm:{name}_total(?:\{{[^}}]*\}})?\s+([^\s]+)", payload, re.MULTILINE
        )
        values = [float(value) for value in matches]
        if not values or any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError(f"Missing or invalid vLLM {name} counters")
        value = sum(values, 0.0)
        if not value.is_integer():
            raise ValueError("Token counters must be integral")
        totals.append(int(value))
    return TokenCounters(*totals)


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def stop(process: subprocess.Popen[bytes]) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def run(config: FinalEvalConfig) -> None:
    job_id = os.environ.get("SLURM_JOB_ID", "")
    allocation = set(os.environ.get("SEARCH_SLURM_JOB_INFO", "").split())
    if not job_id or {f"JobId={job_id}", "Reservation=rsi"} - allocation:
        raise ValueError("Final evaluation requires a host-verified rsi allocation")
    gpus = os.environ.get("CUDA_VISIBLE_DEVICES", "").split(",")
    if len(gpus) != 3 or not all(gpus):
        raise ValueError("Original H0 evaluation uses three allocated GPUs")
    repository, data = Path(config.benchmark_repository), Path(config.data_root)
    historical = json.loads(Path(config.historical_environment).read_text())
    source_names = (
        "local/dense_mcp.py",
        "local/run_react_batch.py",
        "local/react_reproduction.json",
        "search_agent/qwen_client.py",
        "search_agent/prompts.py",
        "searcher/tools.py",
        "scripts_evaluation/evaluate_run.py",
    )
    for name in source_names:
        if digest(repository / name) != historical["source_sha256"][name]:
            raise ValueError(f"H0 source differs from the historical baseline: {name}")
    queries = data / "datasets/queries.tsv"
    if digest(queries) != historical["queries_sha256"]:
        raise ValueError("Official query set changed")
    with queries.open() as source:
        identifiers = [row[0] for row in csv.reader(source, delimiter="\t")]
    expected = set(identifiers)
    if len(identifiers) != 830 or len(expected) != 830:
        raise ValueError("Final comparison requires 830 unique queries")
    manifest = Path(config.checkpoint_manifest)
    if digest(manifest) != config.checkpoint_manifest_sha256:
        raise ValueError("Frozen model manifest changed")
    for entry in json.loads(manifest.read_text())["files"]:
        if digest(Path(config.model) / entry["path"]) != entry["sha256"]:
            raise ValueError("Frozen checkpoint changed after selection")
    lock_path = Path(config.asset_lock)
    if digest(lock_path) != config.asset_lock_sha256:
        raise ValueError("Frozen evaluation asset lock changed")
    asset_lock = json.loads(lock_path.read_text())
    required_roots = {
        str(Path(path).resolve())
        for path in (
            config.base_model,
            config.embedding_model,
            config.judge_model,
            config.qwen_overlay,
            str(Path(__file__).resolve().parents[2]),
            str(data / "indexes/qwen3-embedding-8b"),
            str(data / "datasets/corpus/data"),
            str(data / "datasets"),
            str(data / "tokenizer"),
        )
    }
    observed_roots = set()
    for asset in asset_lock["assets"]:
        asset_manifest = Path(asset["manifest"])
        if digest(asset_manifest) != asset["sha256"]:
            raise ValueError("An evaluation asset manifest changed")
        snapshot = json.loads(asset_manifest.read_text())
        asset_root = Path(snapshot["root"]).resolve()
        observed_roots.add(str(asset_root))
        for entry in snapshot["files"]:
            path = asset_root / entry["path"]
            if path.stat().st_size != entry["size"] or digest(path) != entry["sha256"]:
                raise ValueError(f"Frozen evaluation asset changed: {path}")
    if observed_roots != required_roots:
        raise ValueError("Evaluation asset lock does not cover the exact required inputs")
    output = Path(config.output)
    output.mkdir(parents=True, exist_ok=False)
    (output / "config.json").write_text(json.dumps(asdict(config), indent=2) + "\n")
    run_directory = output / "runs/qwen3-embedding-8b" / output.name
    evaluation_root = output / "evals"
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [config.qwen_overlay, str(Path(__file__).resolve().parents[2]), str(repository)]
        ),
        "BCP_SNIPPET_TOKENIZER": str(data / "tokenizer"),
        "QWEN_AGENT_MAX_LLM_CALL_PER_RUN": "50",
        "QWEN_AGENT_DEFAULT_MAX_INPUT_TOKENS": "115000",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "OMP_NUM_THREADS": "8",
        "TOKENIZERS_PARALLELISM": "false",
        "NO_PROXY": "*",
        "no_proxy": "*",
        "LD_LIBRARY_PATH": str(data / "tools/python310/lib") + ":/.singularity.d/libs",
    }
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        environment.pop(name, None)
    versions = subprocess.check_output(
        [
            config.data_python,
            "-c",
            "from importlib.metadata import version; "
            "assert version('qwen-agent') == '0.0.27'; print(version('qwen-agent'))",
        ],
        env=environment,
        text=True,
    ).strip()
    (output / "environment.json").write_text(
        json.dumps(
            {
                "slurm_job_id": job_id,
                "qwen_agent": versions,
                "effective_top_k": 20,
                "seed": config.seed,
                "historical_source_hashes_verified": True,
                "asset_lock_sha256": config.asset_lock_sha256,
                "asset_hashes_verified": True,
            },
            indent=2,
        )
        + "\n"
    )
    ports: list[int] = []
    while len(ports) < 3:
        port = free_port()
        if port not in ports:
            ports.append(port)
    dense_port, *solver_ports = ports
    processes: list[subprocess.Popen[bytes]] = []

    def launch(arguments: list[str], gpu: str, name: str, *, vllm: bool = False) -> None:
        local = {**environment, "CUDA_VISIBLE_DEVICES": gpu}
        if vllm:
            local["LD_LIBRARY_PATH"] = "/.singularity.d/libs"
        with (output / f"{name}.log").open("wb") as log:
            processes.append(
                subprocess.Popen(
                    arguments,
                    cwd=repository,
                    env=local,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            )

    started = time.monotonic()
    try:
        launch(
            [
                config.data_python,
                str(repository / "local/dense_mcp.py"),
                "--model",
                config.embedding_model,
                "--index",
                str(data / "indexes/qwen3-embedding-8b/corpus.shard*.pkl"),
                "--corpus",
                str(data / "datasets/corpus/data/*.parquet"),
                "--port",
                str(dense_port),
            ],
            gpus[0],
            "dense",
        )
        for index, port in enumerate(solver_ports):
            launch(
                [
                    config.vllm_python,
                    "-m",
                    "vllm.entrypoints.openai.api_server",
                    "--model",
                    config.model,
                    "--tokenizer",
                    config.base_model,
                    "--generation-config",
                    config.base_model,
                    "--served-model-name",
                    "Qwen/Qwen3-8B",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--reasoning-parser",
                    "qwen3",
                    "--max-model-len",
                    "131072",
                    "--hf-overrides",
                    json.dumps(
                        {
                            "max_position_embeddings": 131072,
                            "rope_scaling": {
                                "rope_type": "yarn",
                                "factor": 4.0,
                                "original_max_position_embeddings": 32768,
                            },
                        }
                    ),
                    "--gpu-memory-utilization",
                    "0.85",
                    "--tensor-parallel-size",
                    "1",
                    "--max-num-seqs",
                    "32",
                    "--seed",
                    str(config.seed),
                ],
                gpus[index + 1],
                f"solver-{index + 1}",
                vllm=True,
            )
        urls = [f"http://127.0.0.1:{port}/v1/models" for port in solver_ports] + [
            f"http://127.0.0.1:{dense_port}/mcp"
        ]
        for _ in range(600):
            if any(process.poll() is not None for process in processes):
                raise RuntimeError("An evaluation service exited during startup")
            try:
                for url in urls:
                    with urllib.request.urlopen(url, timeout=2) as response:
                        if response.status != 200:
                            raise RuntimeError("Service readiness failed")
                break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(3)
        else:
            raise TimeoutError("Evaluation services did not become ready")
        before = [counters(port) for port in solver_ports]
        inference_started = time.monotonic()
        with (output / "agent.log").open("wb") as log:
            subprocess.run(
                [
                    config.data_python,
                    str(repository / "local/run_react_batch.py"),
                    "--queries",
                    str(queries),
                    "--output-dir",
                    str(run_directory),
                    "--model-servers",
                    *(f"http://127.0.0.1:{port}/v1" for port in solver_ports),
                    "--mcp-url",
                    f"http://127.0.0.1:{dense_port}/mcp",
                    "--workers",
                    "24",
                    "--max-tokens",
                    "10000",
                    "--seed",
                    str(config.seed),
                ],
                env=environment,
                cwd=repository,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
        after = [counters(port) for port in solver_ports]
        if any(
            last.input_tokens < first.input_tokens or last.output_tokens < first.output_tokens
            for first, last in zip(before, after, strict=True)
        ):
            raise ValueError("Solver counters reset during the evaluation")
        metrics = {
            "input_tokens": sum(
                last.input_tokens - first.input_tokens
                for first, last in zip(before, after, strict=True)
            ),
            "output_tokens": sum(
                last.output_tokens - first.output_tokens
                for first, last in zip(before, after, strict=True)
            ),
            "inference_wall_seconds": time.monotonic() - inference_started,
            "token_source": "vLLM0.17 Prometheus: full prompt including cache; generation tokens",
            "before": [asdict(value) for value in before],
            "after": [asdict(value) for value in after],
        }
        (output / "solver-cost.json").write_text(json.dumps(metrics, indent=2) + "\n")
    finally:
        for process in processes:
            stop(process)
    judge_environment = {
        **environment,
        "CUDA_VISIBLE_DEVICES": gpus[1],
        "LD_LIBRARY_PATH": "/.singularity.d/libs",
    }
    with (output / "judge.log").open("wb") as log:
        subprocess.run(
            [
                config.vllm_python,
                str(repository / "scripts_evaluation/evaluate_run.py"),
                "--input_dir",
                str(run_directory),
                "--ground_truth",
                str(data / "datasets/browsecomp_plus_decrypted.jsonl"),
                "--qrel_evidence",
                str(repository / "topics-qrels/qrel_evidence.txt"),
                "--eval_dir",
                str(evaluation_root),
                "--model",
                config.judge_model,
                "--tensor_parallel_size",
                "1",
                "--batch_size",
                "16",
            ],
            env=judge_environment,
            cwd=repository,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )
    summary, _ = summarize_evaluation(config, elapsed_seconds=time.monotonic() - started)
    (output / "result.json").write_text(json.dumps(asdict(summary), indent=2) + "\n")
    print(json.dumps(asdict(summary), indent=2), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--config", type=Path)
    mode.add_argument("--export-checkpoint", type=Path, help="Original checkpoint manifest")
    mode.add_argument(
        "--recover-summary", type=Path, help="Original config of a terminal evaluation"
    )
    parser.add_argument("--source-sha256", help="Frozen original checkpoint manifest SHA256")
    parser.add_argument(
        "--output", type=Path, help="New export directory or recovered summary JSON"
    )
    arguments = parser.parse_args()
    if arguments.config:
        if arguments.output or arguments.source_sha256:
            parser.error("--output and --source-sha256 are only for checkpoint export")
        run(FinalEvalConfig(**json.loads(arguments.config.read_text())))
    elif arguments.recover_summary:
        if arguments.output is None or arguments.source_sha256:
            parser.error("--recover-summary requires --output and does not accept --source-sha256")
        print(recover_summary(arguments.recover_summary, arguments.output), flush=True)
    else:
        if arguments.output is None or not arguments.source_sha256:
            parser.error("--export-checkpoint requires --output and --source-sha256")
        print(
            export_checkpoint(
                arguments.export_checkpoint,
                arguments.output,
                source_sha256=arguments.source_sha256,
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
