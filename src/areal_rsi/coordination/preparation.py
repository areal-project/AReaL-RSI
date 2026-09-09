"""Optional adapters for the same conversation-led execution setup."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from areal_rsi.base.storage import write_json
from areal_rsi.coordination.config import ResearchConfig, load_config
from areal_rsi.coordination.coordinator import Coordinator


async def prepare_example(
    coordinator: Coordinator, repository: Path, arguments: dict[str, Any], key: str
) -> ResearchConfig:
    from areal_rsi.coordination.intake import reject_credentials

    adapter = arguments["adapter"]
    if adapter not in {"kernelbench", "cuhkx"}:
        raise ValueError("没有这个内置适配器；其他任务可接入普通命令或导入执行配置")
    settings = json.loads(arguments["settings_json"])
    if not isinstance(settings, dict):
        raise ValueError("环境配置必须是 JSON 对象")
    reject_credentials(settings)
    folder = coordinator.store.root / "setup" / key
    output = folder / "execution.json"
    if output.exists():
        config = load_config(output)
        if adapter == "kernelbench":
            config.execution_env = {
                "RSI_TARGET_MODEL": arguments["target_model"],
                "RSI_TARGET_BASE_URL": arguments["target_base_url"],
            }
        return config
    folder.mkdir(parents=True, exist_ok=True)
    seed = settings.pop("seed_dir", None)
    source = settings.pop("repository", None)
    selection_file = settings.pop("dataset_config", None)
    if adapter == "kernelbench" and not settings.get("prepared_data"):
        if not source or not selection_file:
            raise ValueError("请提供已准备的题池路径，或本机 KernelBench 仓库和题池选择文件")
        source_path, selection_path = Path(source), Path(selection_file)
        if not source_path.is_absolute() or not selection_path.is_absolute():
            raise ValueError("仓库和题池选择文件必须使用绝对路径")
        from areal_rsi.adapters.kernelbench import REVISION, prepare_dataset

        selections = json.loads(selection_path.read_text())
        prepared = folder / "dataset"
        if not prepared.exists():
            await asyncio.to_thread(
                prepare_dataset,
                source_path,
                prepared,
                selections["tasks"],
                revision=selections.get("revision", REVISION),
            )
        settings["prepared_data"] = str(prepared)
    operator = folder / "operator.json"
    write_json(operator, settings, immutable=True)
    brief = coordinator.store.brief()
    command = [
        sys.executable,
        str(repository / "scripts" / "configure_research.py"),
        "--example",
        adapter,
        "--operator",
        str(operator),
        "--output",
        str(output),
    ]
    for name in (
        "max_agents",
        "max_nodes",
        "max_model_calls",
        "max_total_tokens",
        "max_verify_calls",
        "max_jobs",
        "max_wall_seconds",
    ):
        command += ["--" + name.replace("_", "-"), str(getattr(brief.limits, name))]
    if seed:
        command += ["--seed", seed]
    if brief.verification and brief.verification.target is not None:
        command += ["--target", str(brief.verification.target)]
    if adapter == "kernelbench":
        if not arguments["target_model"] or not arguments["target_base_url"]:
            raise ValueError("还需要目标模型名称和接口地址")
        command += ["--target-model", arguments["target_model"]]
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=repository,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, error = await asyncio.wait_for(process.communicate(), timeout=600)
    except (asyncio.CancelledError, TimeoutError):
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode:
        # configure_research already removes arbitrary verifier/SSH stderr.
        raise ValueError(error.decode(errors="replace")[-4000:])
    config = load_config(output)
    if adapter == "kernelbench":
        config.execution_env = {
            "RSI_TARGET_MODEL": arguments["target_model"],
            "RSI_TARGET_BASE_URL": arguments["target_base_url"],
        }
        write_json(output, config.public())
    return config
