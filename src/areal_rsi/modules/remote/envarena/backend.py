"""EnvArena provider assembly and its read-only Job projection."""

from __future__ import annotations

import json
from pathlib import Path

from areal_rsi.api.base import SolutionVersion, VerifierSpec
from areal_rsi.base.runs import RunPaths
from areal_rsi.base.storage import decode, encode, new_id, safe_name, write_json
from areal_rsi.modules.remote.base import RemoteBackend, RemoteContext, RemoteJob
from areal_rsi.modules.remote.envarena.benchmark import ArenaBenchmark
from areal_rsi.modules.remote.envarena.client import ArenaClient, ArenaConnection
from areal_rsi.modules.remote.envarena.config import ArenaConfig
from areal_rsi.modules.remote.envarena.harness import (
    REMOTE_HARNESS_TYPE,
    ArenaHarnessBuilder,
    snapshot,
)
from areal_rsi.modules.remote.envarena.runtime import (
    ArenaEvaluation,
    ArenaHarnessRuntime,
    ArenaVerifier,
)


class EnvArenaProvider:
    environment_names = (
        "RSI_ARENA_CONFIG",
        "ARENA_OPENAPI_TOKEN",
        "ENVARENA_API_TOKEN",
        "ARENA_PROFILE",
        "ARENA_BASE_URL",
    )

    async def create(self, context: RemoteContext, config_path: Path) -> RemoteBackend:
        client = ArenaClient(ArenaConnection.from_environment())
        config = await ArenaConfig.load(config_path, client)
        benchmark = ArenaBenchmark(client, config)
        await benchmark.load()
        remote_model = await client.version("models", config.model)
        await client.version("harnesses", config.harness)
        baseline_file = context.root / "baseline.json"
        if baseline_file.exists():
            baseline = decode(SolutionVersion, json.loads(baseline_file.read_text()))
            if baseline.harness.content.media_type != REMOTE_HARNESS_TYPE:
                raise ValueError("数据目录不是 EnvArena 实验，请设置独立的 RSI_DATA_ROOT")
        resolved = {"base_url": client.connection.base_url, "config": encode(config)}
        config_file = context.root / "remote" / "config.json"
        if config_file.exists():
            previous = json.loads(config_file.read_text())
            previous.pop("research_model", None)
            current_binding = json.loads(json.dumps(resolved))
            for value in (previous, current_binding):
                value["config"].pop("poll_seconds", None)
                value["config"].pop("timeout_seconds", None)
            if previous != current_binding:
                raise ValueError("remote 评测配置已改变，请使用独立数据目录")
        else:
            write_json(config_file, resolved, immutable=True)
        write_json(
            context.root / "remote" / "observers" / f"{new_id('observer')}.json",
            {"poll_seconds": config.poll_seconds, "timeout_seconds": config.timeout_seconds},
            immutable=True,
        )
        if not baseline_file.exists():
            model = await context.versions.create_model(
                remote_model["spec"]["model_name"],
                await context.content.put_json(
                    {
                        "backend": "envarena",
                        "ref": encode(config.model),
                        "content_hash": remote_model.get("content_hash"),
                    }
                ),
            )
            harness = await context.versions.create_harness(
                config.harness["key"], await snapshot(client, context.content, config.harness)
            )
            baseline = await context.versions.create_solution(model, harness)
            write_json(baseline_file, baseline, immutable=True)

        runtime = ArenaHarnessRuntime(
            client,
            context.content,
            context.trajectories,
            benchmark,
            config,
            context.root / "remote",
            context.emit,
        )

        def bind_run(paths: RunPaths) -> None:
            # Bind task receipts to the owning research run.
            runtime.root = paths.runtime / "remote"

        return RemoteBackend(
            identity={
                "base_url": client.connection.base_url,
                "benchmark": encode(config.benchmark),
                "model": encode(config.model),
                "harness": encode(config.harness),
                "allowed_changes": list(config.allowed_changes),
            },
            baseline=baseline,
            benchmark=benchmark,
            harness_runtime=runtime,
            bind_run=bind_run,
            verifier=ArenaVerifier(context.content),
            verifier_spec=VerifierSpec(
                verifier_id=(
                    f"{benchmark.spec['reward_ref']['key']}@"
                    f"{benchmark.spec['reward_ref']['version']}"
                ),
                provider="injected",
                config={"reward_ref": benchmark.spec["reward_ref"]},
            ),
            evaluation=ArenaEvaluation(config.target_score),
            harness_builder=ArenaHarnessBuilder(client, context.content, config),
            description=(
                f"EnvArena {config.benchmark['key']}@{config.benchmark['version']}；"
                f"固定被测模型 {baseline.model.name} 和远端 Runtime，仅修改 "
                f"{', '.join(config.allowed_changes)}。本地研究模型与被测模型独立。"
            ),
        )

    def read_jobs(self, root: Path, run_id: str) -> list[RemoteJob]:
        jobs = []
        paths = RunPaths(root, run_id)
        folders = (
            root / "remote",
            paths.runtime / "remote",
            *(paths.root / "execution-outputs").glob("*/private/remote"),
        )
        for path in sorted(
            p for folder in folders for p in (folder / "executions").glob("*/receipt.json")
        ):
            record = json.loads(path.read_text())
            if record.get("signature", {}).get("run_id") != run_id:
                continue
            job_id = record.get("job_id")
            production = record["signature"].get("base_url") == (
                "https://htnn-gateway.alipay.com/envarena/openapi/v1"
            )
            jobs.append(
                RemoteJob(
                    job_id=job_id,
                    state=record.get("state", "prepared"),
                    task_counts=record.get("task_counts", {}),
                    url=f"https://envarena-prod.alipay.com/jobs/{job_id}"
                    if production and isinstance(job_id, str) and safe_name(job_id)
                    else None,
                )
            )
        return jobs
