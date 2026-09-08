"""Opt-in real CPU integration smoke; requires existing synthetic data and Torch.

Exercises the application/tool/job/verifier/export bridge without constructing a
model runtime. This deliberately does not run in the default pytest suite.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

from areal_rsi.api.research import CandidateSnapshot, VerifiedResult
from areal_rsi.coordination.actions import ResearchActions
from areal_rsi.coordination.config import ResearchConfig, load_config
from areal_rsi.coordination.jobs import ResearchExecution
from areal_rsi.coordination.verification import read_trusted_result
from areal_rsi.modules.responses import ResponsesRuntime, ToolCall, ToolResult
from areal_rsi.research_app import ResearchApp

REPOSITORY = Path(__file__).resolve().parents[1]


def write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n")


async def smoke(output: Path, prepared: Path, training_python: Path) -> dict[str, Any]:
    output = output.resolve()
    protocol = json.loads((prepared / "protocol.json").read_text())
    if protocol.get("dataset_kind") != "synthetic":
        raise ValueError("This local smoke accepts only explicitly synthetic prepared data")
    if not training_python.is_absolute() or not training_python.is_file():
        raise ValueError(
            "--training-python must name the existing absolute Torch Python executable"
        )
    output.mkdir(parents=True, exist_ok=False)
    seed = output / "seed"
    shutil.copytree(REPOSITORY / "examples/cuhkx/seed", seed)
    write(
        seed / "training.json",
        {
            "epochs": 10,
            "frames": 2,
            "image_size": 16,
            "width": 4,
            "batch_size": 2,
            "learning_rate": 0.01,
        },
    )
    operator = output / "operator.json"
    write(
        operator,
        {
            "prepared_data": str(prepared.resolve()),
            "runner": {"kind": "local", "device": "cpu", "python": str(training_python)},
            "training": {"timeout_seconds": 120},
            "verification": {"timeout_seconds": 60},
            "phases": {"search": "validate", "final": "final"},
        },
    )
    configuration = output / "research.json"
    subprocess.run(
        [
            sys.executable,
            str(REPOSITORY / "scripts/configure_research.py"),
            "--example",
            "cuhkx",
            "--operator",
            str(operator),
            "--output",
            str(configuration),
            "--seed",
            str(seed),
            "--allow-local",
            "--max-agents",
            "1",
            "--max-wall-seconds",
            "600",
        ],
        check=True,
        cwd=REPOSITORY,
        timeout=60,
    )
    config = load_config(configuration)
    runtime_attempts = 0

    def forbidden_runtime(_: ResearchConfig) -> ResponsesRuntime:
        nonlocal runtime_attempts
        runtime_attempts += 1
        raise AssertionError("The tool-driven smoke must not construct a model runtime")

    app = ResearchApp(output / "data", config=config, runtime_factory=forbidden_runtime)
    detail = await app.create({"simulated": False})
    c = app.coordinator(detail["run_id"])
    assert detail["baseline"] is detail["best"] is None
    assert c.state.get("baseline_candidate_id") is None
    c.start()
    c.begin_turn("rsi")
    actions = ResearchActions(c)
    await actions.initialize_workspace("rsi")
    execution = ResearchExecution(
        actions,
        commands={item.name: item for item in config.commands},
        verifier=config.verifier,
        read_verification=app._verification_reader(c, config),
        simulated=False,
    )
    trace: list[dict[str, Any]] = []
    sequence = 0

    async def call(agent: str, name: str, **arguments: Any) -> ToolResult:
        nonlocal sequence
        sequence += 1
        result = await actions.handler(agent)(ToolCall(name, arguments, f"smoke-{sequence}"))
        trace.append(
            {
                "agent": agent,
                "tool": name,
                "arguments": arguments,
                "output": result.output,
                "stop": result.stop,
                "reason": result.reason,
            }
        )
        write(output / "tool-trace.json", trace)
        if isinstance(result.output, dict) and result.output.get("error"):
            raise AssertionError(f"{name}: {result.output['error']}")
        return result

    async def complete_job(agent: str, job_id: str) -> dict[str, Any]:
        wait = await call(agent, "wait_for_job", job_id=job_id)
        if wait.stop:
            c.end_turn(agent)
        deadline = time.monotonic() + 180
        while not execution.jobs[job_id]["processed"]:
            await execution.poll()
            if time.monotonic() > deadline:
                raise TimeoutError(f"CPU integration job did not finish: {job_id}")
            await asyncio.sleep(0.05)
        if wait.stop:
            assert agent in c.pending(), "Job completion must wake its waiting agent"
            c.begin_turn(agent)
        result = await call(agent, "job_status", job_id=job_id)
        assert result.output["state"] == "succeeded", result.output
        return dict(result.output)

    try:
        spawned = await call(
            "rsi",
            "spawn_researcher",
            direction="Synthetic CPU bridge acceptance",
            instruction="Train a tiny CNN, collect its checkpoint and verify it.",
            completion_criteria="Search and final records match the actual frozen checkpoint.",
            references=[],
        )
        agent = spawned.output["agent_id"]
        c.end_turn("rsi")
        c.begin_turn(agent)
        session_id = c.actor(agent).session_id
        training = await call(
            agent, "run_job", command_name="train", candidate_id="", parameters_json="{}"
        )
        train_job = training.output["job_id"]
        await complete_job(agent, train_job)
        assert c.store.run().best_result_ref is None, "Training stdout must not become a score"
        files = await call(agent, "read_job_output_file", job_id=train_job, path="", offset=0)
        assert "checkpoint.pt" in files.output["files"]
        assert all("private" not in path for path in files.output["files"])
        training_info = await call(
            agent, "read_job_output_file", job_id=train_job, path="config.json", offset=0
        )
        assert training_info.output["uri"].startswith("run://")
        await call(agent, "collect_job_output", job_id=train_job, destination="trained")
        workspace = actions.workspace(agent)
        checkpoint = workspace / "trained/checkpoint.pt"
        assert checkpoint.is_file() and not (workspace / "trained/private").exists()
        candidate_result = await call(
            agent, "save_candidate", description="Real tiny CNN trained on synthetic clips"
        )
        candidate_id = candidate_result.output["candidate_id"]
        candidate = c.store.get("candidates", candidate_id, CandidateSnapshot)
        scores: dict[str, Any] = {}
        for phase in ("search", "final"):
            submitted = await call(
                agent, "verify_candidate", candidate_id=candidate_id, phase=phase
            )
            job_id = submitted.output["job_id"]
            completed = await complete_job(agent, job_id)
            verified = completed["result"]
            assert verified["status"] == "succeeded", verified
            assert verified["candidate_id"] == candidate_id and verified["simulated"] is False
            measured = json.loads(
                (c.store.root / "execution-outputs" / job_id / "evaluation.json").read_text()
            )
            reread = read_trusted_result(
                c.store,
                execution.manager.get(job_id),
                candidate,
                phase,
                identity=config.metadata["verification_identity"],
            )
            assert reread.score == measured["score"] == verified["score"]
            assert measured["dataset_kind"] == "synthetic" and measured["simulated"] is False
            assert (
                measured["checkpoint_sha256"] == hashlib.sha256(checkpoint.read_bytes()).hexdigest()
            )
            scores[phase] = {
                "job_id": job_id,
                "score": verified["score"],
                "result_id": reread.result_id,
                "protocol_id": measured["protocol_id"],
                "manifest_id": measured["manifest_id"],
                "candidate_sha256": measured["candidate_sha256"],
                "checkpoint_sha256": measured["checkpoint_sha256"],
            }
        assert scores["search"]["manifest_id"] != scores["final"]["manifest_id"]
        assert c.actor(agent).session_id == session_id and len(c.actor(agent).iterations) == 1
        report = await call(
            agent,
            "submit_report",
            task_status="completed",
            work="Trained a tiny CNN, collected trained/, froze a candidate and ran both splits.",
            problems="None in the CPU bridge; CUDA/container isolation is outside this smoke.",
            results=(
                f"Synthetic search={scores['search']['score']}, final={scores['final']['score']}."
            ),
            interpretation="Proves integration and identity checks; not competition performance.",
            next_steps="Stop this synthetic direction and export the verified candidate.",
            shared_findings=[],
            references=[candidate_result.output["uri"]],
        )
        assert report.stop and report.reason == "rsi_reply"
        c.end_turn(agent)
        assert agent not in c.pending()
        c.begin_turn("rsi")
        await call(
            "rsi", "stop_researcher", agent_id=agent, reason="Synthetic bridge acceptance complete"
        )
        await call(
            "rsi",
            "finish",
            candidate_id=candidate_id,
            summary="Real CPU synthetic bridge passed; no model API or competition score claim.",
        )
        c.end_turn("rsi")
        final_detail = await app.detail(c.store.run_id)
        assert final_detail["status"] == "succeeded" and final_detail["baseline"] is None
        best = c.store.get("verifications", scores["search"]["job_id"], VerifiedResult)
        run = c.store.run()
        assert run.best_result_ref is not None and run.final_result_ref is not None
        assert c.store.public_record(run.best_result_ref)["result_id"] == best.result_id
        assert (
            c.store.public_record(run.final_result_ref)["result_id"] == scores["final"]["result_id"]
        )
        download = await app.call("export", {"run_id": c.store.run_id})
        archive_path = output / "verified-candidate.zip"
        shutil.copy2(download.path, archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            exported = json.loads(archive.read("research-result.json"))
            assert exported["candidate_id"] == candidate_id
            assert exported["verification"]["phase"] == "final"
            assert exported["verification"]["score"] == scores["final"]["score"]
            assert archive.read("candidate/trained/checkpoint.pt") == checkpoint.read_bytes()
            assert not any("private" in name or "labels" in name for name in archive.namelist())
        assert runtime_attempts == 0
        summary = {
            "status": "passed",
            "dataset_kind": "synthetic",
            "simulated": False,
            "model_api_calls": 0,
            "model_runtime_constructions": runtime_attempts,
            "gpu_jobs": 0,
            "run_id": c.store.run_id,
            "run_root": str(c.store.root),
            "research_config": str(configuration),
            "candidate_id": candidate_id,
            "candidate_digest": candidate.content.digest,
            "results": scores,
            "export_zip": str(archive_path),
            "export_sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
            "agent_session_preserved": True,
            "baseline": None,
            "limitation": "Synthetic CPU only; no Kaggle result, CUDA or Docker isolation claim.",
        }
        write(output / "summary.json", summary)
        write(output / "public-detail.json", final_detail)
        return summary
    finally:
        execution.close(cancel=True)
        await app.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--training-python", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(smoke(args.output, args.prepared, args.training_python))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
