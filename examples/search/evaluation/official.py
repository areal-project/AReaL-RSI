"""Execute frozen H0 inference and the official judge, or dispatch offline tools."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict
from pathlib import Path

from areal_rsi.infra.execution import terminate_process_tree
from examples.search import PROJECT_ROOT
from examples.search.evaluation.artifacts import (
    FinalEvalConfig,
    TokenCounters,
    digest,
)
from examples.search.evaluation.checkpoints import export_checkpoint
from examples.search.evaluation.verification import (
    recover_summary,
    summarize_evaluation,
    verify_solver_outputs,
)


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
    released = terminate_process_tree(process.pid)
    process.wait(timeout=5)
    if not released:
        raise RuntimeError(f"Evaluation service process tree cleanup incomplete: {process.pid}")


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
            str(PROJECT_ROOT),
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
        "PYTHONPATH": os.pathsep.join([config.qwen_overlay, str(PROJECT_ROOT), str(repository)]),
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
            batch_result = subprocess.run(
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
                check=False,
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
            "source_batch_exit_code": batch_result.returncode,
            "before": [asdict(value) for value in before],
            "after": [asdict(value) for value in after],
        }
        (output / "solver-cost.json").write_text(json.dumps(metrics, indent=2) + "\n")
    finally:
        # Clean every service even if one tree cannot be released. The judge
        # must not start while an observed inference descendant still survives.
        errors = []
        for process in processes:
            try:
                stop(process)
            except (RuntimeError, subprocess.TimeoutExpired) as error:
                errors.append(str(error))
        if errors:
            raise RuntimeError("; ".join(errors))
    # Preserve counters even when the legacy batch's JSON-only observation
    # check exits nonzero. Only proven local model-action errors may continue.
    from areal_rsi.infra.artifacts import write_record

    write_record(output / "solver-verification.json", verify_solver_outputs(config))
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
    summary, _ = summarize_evaluation(
        config, elapsed_seconds=time.monotonic() - started, allow_context_budget_exhaustion=True
    )
    (output / "result.json").write_text(json.dumps(asdict(summary), indent=2) + "\n")
    print(json.dumps(asdict(summary), indent=2), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--config", type=Path)
    mode.add_argument(
        "--verify-runs", type=Path, help="Original config for read-only solver verification"
    )
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
    elif arguments.verify_runs:
        if arguments.output is None or arguments.source_sha256:
            parser.error("--verify-runs requires --output and does not accept --source-sha256")
        from areal_rsi.infra.artifacts import read_record, write_record

        config = read_record(arguments.verify_runs, FinalEvalConfig)
        if arguments.output.exists() or arguments.output.resolve().is_relative_to(
            Path(config.output).resolve()
        ):
            parser.error("Use a new verification receipt outside the source directory")
        write_record(arguments.output, verify_solver_outputs(config))
        print(arguments.output, flush=True)
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
