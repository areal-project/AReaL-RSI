"""One Search GPU stage; candidate coordination belongs to ResearchSystem."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import signal
import socket
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from areal_rsi.api.control import decode_record, encode_record
from areal_rsi.api.records import EvaluationSample, TraceEvent
from areal_rsi.infra.artifacts import file_ref, read_record, write_record
from areal_rsi.infra.execution import JobExecutor
from examples.search.problem import StageRequest


@dataclass(frozen=True, slots=True)
class OfficialStep:
    type: Literal["tool_call", "reasoning", "output_text"]
    output: str | tuple[str, ...] | None
    tool_name: str | None = None
    arguments: str | None = None


def official_trace(source: Path, query: str) -> tuple[str, tuple[TraceEvent, ...]]:
    """Preserve the original H0 result sequence and the judge's exact final output."""
    raw = json.loads(source.read_text())
    if not isinstance(raw["result"], list):
        raise ValueError("Official solver result must be a sequence")
    steps = tuple(decode_record(OfficialStep, item) for item in raw["result"])
    events = [TraceEvent("prompt", query)]
    for step in steps:
        if step.type == "tool_call":
            if step.output is not None and not isinstance(step.output, str):
                raise ValueError("Official tool output must be text")
            events.extend(
                (
                    TraceEvent(
                        "tool_call",
                        json.dumps({"name": step.tool_name, "arguments": step.arguments}),
                    ),
                    TraceEvent("tool_result", step.output or ""),
                )
            )
        else:
            content = "\n".join(step.output) if isinstance(step.output, tuple) else step.output
            events.append(TraceEvent("generation", content or ""))
    response = steps[-1].output if steps and steps[-1].type == "output_text" else ""
    if not isinstance(response, str) or any(not isinstance(event.content, str) for event in events):
        raise ValueError("Official text and tool outputs must be text")
    return response, tuple(events)


async def wait_for_dense(process: asyncio.subprocess.Process, url: str) -> None:
    import urllib.error
    import urllib.request

    def probe() -> bool:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                return bool(response.status == 200)
        except (urllib.error.URLError, TimeoutError):
            return False

    for _ in range(240):
        if process.returncode is not None:
            raise RuntimeError("Dense service exited before readiness; inspect its log")
        if await asyncio.to_thread(probe):
            return
        await asyncio.sleep(0.5)
    raise TimeoutError("Dense service readiness deadline exceeded")


async def run(stage: StageRequest) -> None:
    allocation = set(os.environ.get("RSI_SLURM_JOB_INFO", "").split())
    job_id = os.environ.get("SLURM_JOB_ID", "")
    if not job_id or {f"JobId={job_id}", f"Reservation={stage.reservation}"} - allocation:
        raise ValueError("Search requires a host-verified Slurm allocation")
    if stage.official is not None:
        await asyncio.to_thread(official, stage)
        return
    request = stage.worker
    assert request is not None
    config = request.config
    gpus = tuple(item for item in os.environ.get("CUDA_VISIBLE_DEVICES", "").split(",") if item)
    required = config.actor_gpus + config.rollout_gpus + 1 if request.mode == "train" else 2
    if len(gpus) < required:
        raise ValueError(f"Search stage requires {required} allocated GPUs")
    split = "train" if request.mode == "train" else Path(request.dataset).stem
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    request = replace(request, mcp_url=f"http://127.0.0.1:{port}/mcp")
    request_path = Path(request.output) / "worker-request.json"
    write_record(request_path, request)
    project = Path(config.project).resolve(strict=True)
    # JobSpec already merges runtime variables and the frozen package/backend paths.
    environment = dict(os.environ)
    policy_gpus = (
        gpus[: config.actor_gpus + config.rollout_gpus] if request.mode == "train" else gpus[:1]
    )
    with (Path(request.output) / "dense.log").open("wb") as log:
        dense = await asyncio.create_subprocess_exec(
            config.dense_python,
            "-m",
            "examples.search.task.retrieval",
            "serve",
            "--corpus",
            str(Path(config.dataset_directory) / f"{split}-corpus.jsonl"),
            "--index",
            str(Path(config.index_directory) / f"{split}.npy"),
            "--model",
            config.embedding_model,
            "--tokenizer",
            config.snippet_tokenizer,
            "--benchmark-repository",
            config.benchmark_repository,
            "--port",
            str(port),
            env={**environment, "CUDA_VISIBLE_DEVICES": gpus[-1]},
            cwd=str(project),
            stdout=log,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        worker: asyncio.subprocess.Process | None = None
        try:
            await wait_for_dense(dense, request.mcp_url)
            worker = await asyncio.create_subprocess_exec(
                config.runtime.python,
                str(project / "examples/search/training/worker.py"),
                "--request",
                str(request_path),
                cwd=str(project),
                env={**environment, "CUDA_VISIBLE_DEVICES": ",".join(policy_gpus)},
                start_new_session=True,
            )
            code = await worker.wait()
            if code:
                raise RuntimeError(f"Search worker failed with exit code {code}")
        finally:
            worker_released = worker is None or await JobExecutor._terminate(worker)
            dense_released = await JobExecutor._terminate(dense)
            if not worker_released or not dense_released:
                raise RuntimeError("Dense service cleanup incomplete")


def official(stage: StageRequest) -> None:
    from examples.search.evaluation.analysis import read_official_results
    from examples.search.evaluation.official import run as evaluate
    from examples.search.evaluation.verification import summarize_evaluation

    config = stage.official
    assert config is not None
    os.environ["SEARCH_SLURM_JOB_INFO"] = os.environ["RSI_SLURM_JOB_INFO"]
    evaluate(config)
    summary, runs = summarize_evaluation(
        config, elapsed_seconds=0, allow_context_budget_exhaustion=True
    )
    with (Path(config.data_root) / "datasets/queries.tsv").open() as query_stream:
        queries = {row[0]: row[1] for row in csv.reader(query_stream, delimiter="\t")}
    correctness = read_official_results(Path(summary.official_results), set(queries))
    output = Path(config.output).parent
    with (output / "samples.jsonl").open("x") as stream:
        for item in runs:
            source = Path(item.source.path)
            response, events = official_trace(source, queries[item.query_id])
            sample = EvaluationSample(
                item.query_id,
                response,
                float(correctness[item.query_id]),
                correctness[item.query_id],
                events,
                {"search_calls": float(item.search_calls), "elapsed_seconds": item.elapsed_seconds},
                (
                    file_ref(source),
                    file_ref(Path(summary.official_results) / (source.stem + "_eval.json")),
                ),
            )
            stream.write(json.dumps(encode_record(sample), ensure_ascii=False) + "\n")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    stage = read_record(parser.parse_args().request, StageRequest)
    task = asyncio.create_task(run(stage))
    for signum in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(signum, task.cancel)
    await task


if __name__ == "__main__":
    asyncio.run(main())
