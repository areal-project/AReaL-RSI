"""One prompt-only ResearchAgent proposal and same-task regeneration."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from run_baseline import DATASET, EXAMPLE, GENERATION_ENVIRONMENT
from run_baseline import main as run_task

from areal_rsi.agents.research.default import DefaultResearchAgent
from areal_rsi.api.base import BenchmarkSplit, ContentRef, EvaluationReport, EvolutionTarget
from areal_rsi.base.storage import (
    FileContentStore,
    FileMemory,
    FileTrajectoryStore,
    FileVersionManager,
    decode,
    new_id,
    now,
    write_json,
)
from areal_rsi.config import AgentModelSettings, ModelConfig
from areal_rsi.modules.gateway import GatewayProxy
from areal_rsi.modules.harness import LocalHarnessBuilder
from areal_rsi.modules.runtime import DshAgentRuntime


def check_baseline_settings(inputs, snapshot):
    if inputs.get("generation_environment") != GENERATION_ENVIRONMENT:
        raise ValueError("Generation environment differs; create a new baseline with explicit ESM")
    if inputs["snapshot"] != snapshot:
        raise ValueError("Runtime snapshot differs from the baseline")
    if inputs.get("max_tokens") != 4096 or inputs.get("timeout_seconds") != 600:
        raise ValueError("Generation settings differ from the baseline")


async def main(baseline: Path):
    baseline = baseline.resolve()
    inputs = json.loads((baseline / "inputs.json").read_text())
    check_baseline_settings(inputs, json.loads((DATASET / "snapshot.json").read_text()))
    validation = json.loads((baseline / "validation.json").read_text())
    target = ModelConfig.from_environment("target")
    old_content = FileContentStore(baseline / "content")
    old_model = decode(ContentRef, inputs["solution"]["model"]["content"])
    if await old_content.read_json(old_model) != target.public_identity():
        raise ValueError("Target connection differs from the baseline")
    output = json.loads((baseline / "task-result.json").read_text())
    source = await old_content.read_bytes(decode(ContentRef, output["output"]))
    if hashlib.sha256(source).hexdigest() != validation["source_sha256"]:
        raise ValueError("Baseline source does not match validation evidence")
    settings = AgentModelSettings.from_environment()
    settings.validate_agents()
    research_connection = settings.resolve("research-agent")
    root = DATASET / "outputs" / new_id("model001_research")
    root.mkdir(parents=True)
    content = FileContentStore(root / "content")
    versions = FileVersionManager(root / "versions")
    traces = FileTrajectoryStore(root / "trajectories", content)
    harness_dir = root / "baseline-harness"
    harness_dir.mkdir()
    original_prompt = await old_content.read_text(
        decode(ContentRef, inputs["solution"]["harness"]["content"])
    )
    (harness_dir / "prompt.md").write_text(original_prompt)
    (harness_dir / "mcp.json").write_text('{"servers": []}\n')
    target_version = await versions.create_model(
        target.model, await content.put_json(target.public_identity())
    )
    harness = await versions.create_harness(
        "baseline-prompt-import", await content.snapshot(harness_dir)
    )
    solution = await versions.create_solution(target_version, harness)
    old_traces = FileTrajectoryStore(baseline / "trajectories", old_content)
    trace = await old_traces.get(output["trajectory"]["trajectory_id"])
    if trace is None:
        raise ValueError("Baseline trajectory is missing")
    evidence = json.loads(await old_content.read_text(trace.content))
    evidence["host_evidence"] = {
        "task": inputs["task"],
        "model_source": source.decode(),
        "validator": validation,
        "validator_stderr": (baseline / "validator.stderr").read_text(),
        "note": "Host-added evidence; not an additional model conversation turn.",
    }
    bundle = await content.put_json(
        {
            "trajectories": [
                {
                    "trajectory_id": trace.trajectory_id,
                    "content": await content.put_json(evidence),
                }
            ]
        }
    )
    score = float(validation["contract_passed"])
    report = EvaluationReport(
        solution.solution_id,
        BenchmarkSplit.TRAIN,
        bool(score),
        score,
        {"contract_v2": score},
        now(),
    )
    model = await versions.create_model(
        research_connection.model, await content.put_json(research_connection.public_identity())
    )
    proxy = GatewayProxy(research_connection.base_url, research_connection.api_key)
    print(f"Research: {research_connection.model}\nRun: {root}", flush=True)
    try:
        runtime = DshAgentRuntime(
            root / "sessions",
            content,
            traces,
            base_url=proxy.url,
            api_key="local-proxy",
            timeout=600,
            max_tokens=12288,
            dsh_root=EXAMPLE.parents[1] / ".rsi/local",
            credential_env_names=settings.credential_env_names,
        )
        agent = DefaultResearchAgent(
            "research-agent-1",
            runtime,
            model,
            content,
            FileMemory(root / "memory"),
            versions,
            root / "research-workspaces",
            editable_path=lambda name: name == "prompt.md",
            harness_description="Only prompt.md is editable. Read host_evidence.validator_stderr "
            "in train/trajectory_0.json. It contains the previous failure. Preserve file output "
            "and Contract V2 precedence. No validator access is available to the Task Agent.",
        )
        change = await agent.propose(
            "Improve reusable model construction instructions using the recorded failure. "
            "Only change prompt.md; do not embed this task, its node names or a solution. "
            "Preserve the original framework constraints. No new tools or dependencies.",
            EvolutionTarget.HARNESS,
            solution,
            report,
            bundle,
        )
        write_json(root / "change.json", change)
        candidate = await LocalHarnessBuilder(content, root / "builds").build(harness, change)
        await content.materialize(candidate, root / "candidate-harness")
    finally:
        proxy.close()
    # Reuse the saved public input rather than a mutable Downloads directory.
    tasks = root / "tasks"
    task_dir = tasks / "001-baseline-snapshot"
    task_dir.mkdir(parents=True)
    (task_dir / "TARGET.md").write_text(inputs["task"]["input"])
    print("Research proposal built. Starting same-model Task Agent.", flush=True)
    candidate_run = await run_task(
        SimpleNamespace(tasks=tasks, prompt=root / "candidate-harness/prompt.md")
    )
    after = json.loads((candidate_run / "validation.json").read_text())
    write_json(
        root / "comparison.json",
        {
            "baseline": str(baseline),
            "candidate": str(candidate_run),
            "baseline_contract_passed": validation["contract_passed"],
            "candidate_contract_passed": after["contract_passed"],
            "scope": "Same training task only, no held-out validation or visual quality score",
        },
    )
    print(f"Comparison: {root / 'comparison.json'}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    asyncio.run(main(parser.parse_args().baseline))
