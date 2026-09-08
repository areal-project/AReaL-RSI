"""One real RSI Task Agent call; no research loop or visual judge."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from adapter import ThreeModelBenchmark

from areal_rsi.api.base import AgentSpec, BenchmarkSplit, TaskResult
from areal_rsi.base.storage import (
    FileContentStore,
    FileTrajectoryStore,
    FileVersionManager,
    new_id,
    write_json,
)
from areal_rsi.config import ModelConfig
from areal_rsi.modules.gateway import GatewayProxy
from areal_rsi.modules.runtime import DshAgentRuntime, sandbox_wrapper

EXAMPLE = Path(__file__).resolve().parent
DATASET = EXAMPLE.parents[1] / ".rsi/datasets/3d-model-v2"


async def main(args):
    connection = ModelConfig.from_environment("target")
    task = (await ThreeModelBenchmark(args.tasks).list_tasks(BenchmarkSplit.TRAIN))[0]
    run = DATASET / "outputs" / new_id("model001_baseline")
    run.mkdir(parents=True)
    workspace = run / "workspace"
    workspace.mkdir()
    template = DATASET / "viewer-template"
    manifest = json.loads((DATASET / "snapshot.json").read_text())
    for name, digest in manifest["files"].items():
        if hashlib.sha256((template / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"Fixed template changed: {name}")
    # Only public framework inputs enter the generator workspace, never validator fixtures.
    for name in ("src/model.js", "CONTRACT_V2.md"):
        destination = workspace / name
        destination.parent.mkdir(exist_ok=True)
        shutil.copy2(template / name, destination)
    (workspace / "TARGET.md").write_text(str(task.input))
    content = FileContentStore(run / "content")
    trajectories = FileTrajectoryStore(run / "trajectories", content)
    versions = FileVersionManager(run / "versions")
    model = await versions.create_model(
        connection.model, await content.put_json(connection.public_identity())
    )
    prompt = await content.put_text((EXAMPLE / "baseline.md").read_text())
    harness = await versions.create_harness("3d-minimal-baseline-v1", prompt)
    solution = await versions.create_solution(model, harness)
    write_json(
        run / "inputs.json",
        {
            "task": task,
            "solution": solution,
            "snapshot": manifest,
            "max_tokens": 4096,
            "timeout_seconds": 600,
        },
    )
    proxy = GatewayProxy(connection.base_url, connection.api_key)
    session = None
    print(f"Run: {run}\nTarget: {connection.model}", flush=True)
    try:
        runtime = DshAgentRuntime(
            run / "sessions",
            content,
            trajectories,
            base_url=proxy.url,
            api_key="local-proxy",
            timeout=600,
            max_tokens=4096,
            dsh_root=EXAMPLE.parents[1] / ".rsi/local",
        )
        session = await runtime.open(
            AgentSpec("task-agent", "deepseek-harness@v1", model, (prompt,), tools=("bash@v1",)),
            workspace_root=str(workspace),
        )
        response = await session.send("Read TARGET.md and implement src/model.js.")
        trace = await session.finish()
        source = workspace / "src/model.js"
        if source.is_symlink() or not source.is_file():
            raise ValueError("Task output is missing or a symlink")
        result = TaskResult(
            await content.put_bytes(source.read_bytes(), media_type="text/javascript"), trace
        )
        write_json(run / "task-result.json", result)
        (run / "response.txt").write_text(response)
        print("Generation finished; validating in isolated runtime.", flush=True)
        evaluation = run / "evaluation"
        subprocess.run(["bash", str(EXAMPLE / "run.sh"), "prepare", str(evaluation)], check=True)
        shutil.copy2(source, evaluation / "src/model.js")
        sandbox = run / "verifier-sandbox"
        sandbox.mkdir()
        node = Path(shutil.which("node")).resolve()
        sandbox_wrapper(sandbox, evaluation, node)
        profile = sandbox / "sandbox.sb"
        profile.write_text(profile.read_text() + "\n(deny network*)\n")
        env = {key: os.environ[key] for key in ("PATH",) if key in os.environ}
        env["GAMEAGENT_GDD_STYLE_ID"] = "piggy-island"
        checked = await asyncio.to_thread(
            subprocess.run,
            ["/usr/bin/sandbox-exec", "-f", str(profile), str(node), "scripts/check-model.mjs"],
            cwd=evaluation,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        (run / "validator.stdout").write_text(checked.stdout)
        (run / "validator.stderr").write_text(checked.stderr)
        write_json(
            run / "validation.json",
            {
                "returncode": checked.returncode,
                "contract_passed": checked.returncode == 0,
                "scope": "Contract V2 only; not TARGET compliance or visual quality",
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            },
        )
        print(f"Validator exit: {checked.returncode}; evidence: {run}", flush=True)
    except Exception as error:
        write_json(run / "failure.json", {"error_type": type(error).__name__})
        raise
    finally:
        if session is not None:
            await session.finish()
        proxy.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, required=True)
    asyncio.run(main(parser.parse_args()))
