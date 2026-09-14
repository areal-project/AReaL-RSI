"""Prepare the fixed eight-task demo without reading any previous research run."""

from __future__ import annotations

import base64
import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from areal_rsi.api.research import ResearchLimits, TaskBrief, VerificationContract
from areal_rsi.base.storage import write_json
from areal_rsi.coordination.config import ResearchConfig
from areal_rsi.execution import ApprovedCommand

HERE = Path(__file__).resolve().parent


def control_request(environment: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    command = [
        environment.get("python", "/opt/.venv/bin/python"),
        "-B",
        "-c",
        (HERE / "control.py").read_text(),
    ]
    if environment.get("continue_script"):
        command = [
            "bash",
            environment["continue_script"],
            str(environment["slurm_job"]),
            "--",
            *command,
        ]
    result = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            environment["host"],
            shlex.join(command),
        ],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=120,
    )
    try:
        response = json.loads(result.stdout)
    except ValueError:
        raise RuntimeError(
            f"et15 连接或容器入口失败（退出码 {result.returncode}），请检查 SSH 与 environment 配置"
        ) from None
    if not response.get("ok"):
        error = response.get("error", "远端准备失败")
        if error.startswith(
            ("TimeoutError:", "URLError:", "ConnectionError:", "RemoteDisconnected:")
        ) or ("HTTP Error 5" in error or "HTTP Error 429" in error):
            raise RuntimeError(error)
        raise ValueError(error)
    return response["value"]


def prepare(root: Path, environment: Path, run_id: str, *, check: bool = False) -> dict[str, Any]:
    demo = json.loads((HERE / "demo.json").read_text())
    env = json.loads(environment.read_text())
    config = ResearchConfig(
        TaskBrief("KernelBench 八题研究", ""),
        model=demo["model"],
        reasoning_effort=demo["reasoning_effort"],
        base_url=demo["base_url"],
    )
    config.pin_model_settings()
    if not check:
        config.runtime()  # Check local credentials before registering any remote tasks.
    setup = root / "runs" / run_id / "setup"
    config_path = setup / "research.json"
    if not check and config_path.exists():
        return json.loads(config_path.read_text())
    payload = {
        "operation": "check" if check else "prepare",
        "run_id": run_id,
        "demo": demo,
        "environment": env,
        "remote_source": (HERE / "remote.py").read_text(),
        "control_source": (HERE / "control.py").read_text(),
    }
    prepared = control_request(env, payload)
    if check:
        return prepared
    manifest = prepared["manifest"]
    manifest_path = setup / "gpuq-manifest.json"
    write_json(manifest_path, manifest, immutable=True)
    write_json(setup / "demo.json", demo, immutable=True)
    seed = setup / "public-inputs"
    for case, ref in prepared["references"].items():
        source = base64.b64decode(ref["source"], validate=True)
        if hashlib.sha256(source).hexdigest() != demo["tasks"][case]["sha256"]:
            raise ValueError("公开题目摘要不匹配")
        folder = seed / "tasks" / case
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "reference.py").write_bytes(source)
        write_json(
            folder / "baseline.json", demo["tasks"][case]["reference_baseline"], immutable=True
        )
    (seed / "RULES.md").write_text((HERE / "RULES.md").read_text())
    common = (sys.executable, str(HERE / "client.py"))
    fixed = ("--manifest", str(manifest_path))
    commands = (
        ApprovedCommand(
            "evaluate_task",
            (
                *common,
                "evaluate",
                *fixed,
                "--workspace",
                "{workspace}",
                "--output",
                "{output}",
                "--task",
                "{task}",
                "--candidate",
                "{candidate}",
            ),
            ("workspace", "output", "task", "candidate"),
        ),
        ApprovedCommand(
            "diagnose_task",
            (
                *common,
                "diagnose",
                *fixed,
                "--workspace",
                "{workspace}",
                "--output",
                "{output}",
                "--task",
                "{task}",
                "--script",
                "{script}",
                "--candidate",
                "{candidate}",
            ),
            ("workspace", "output", "task", "script", "candidate"),
        ),
        ApprovedCommand(
            "collect_bundle", (*common, "collect", *fixed, "--output", "{output}"), ("output",)
        ),
    )
    config.brief = TaskBrief(
        "KernelBench 八题优化 · " + config.model,
        "研究并优化 "
        + "、".join(demo["tasks"])
        + " 的完整 ModelNew。先保证正确性，再提高整题加速比。"
        "阅读自己工作区 RULES.md 和 tasks/<题号>/reference.py；不读取其他 run 或历史解法。"
        "最多五名研究员，RSI 自主决定方向、分工和阶段汇报点；实现和实验交给研究员。"
        "每题最多十次探索提交，包含诊断和失败；最终另由程序独立复测一次。"
        "达到目标或判断无法继续改善时，用 stop_run 结束探索，后台自动收尾。"
        "不用重复请求已给定的环境、资源和目标；不另设总时间或模型调用上限。",
        ResearchLimits(
            max_agents=demo["max_agents"],
            max_nodes=None,
            max_model_calls=None,
            max_total_tokens=None,
            max_wall_seconds=None,
            max_verify_calls=None,
            max_jobs=None,
        ),
        VerificationContract(
            "fast_1",
            target=demo["target"],
            description="八题中正式正确且 speedup > 1 的比例；冻结源码以 seed 137 独立复测",
            identity={"require_final_confirmation": True},
        ),
    )
    config.seed_dir = str(seed)
    config.commands = commands
    config.verifier = ApprovedCommand(
        "verify_bundle",
        (
            *common,
            "verify",
            *fixed,
            "--workspace",
            "{workspace}",
            "--output",
            "{output}",
            "--phase",
            "{phase}",
        ),
        ("workspace", "output", "phase"),
        public_output_subdir="public",
        final_candidate_policy="multiple",
    )
    config.finalizer = ApprovedCommand(
        "final_confirmation",
        (sys.executable, str(HERE / "finish.py"), *fixed, "--output", "{output}"),
        ("output",),
    )
    config.metadata = {
        "auto_resume": True,
        "allow_web_access": False,
        "agent_tool_instructions": (
            "遵守 RULES.md。只使用自己的本地工作区，GPU 仅经登记作业工具调用；"
            "禁止直接 SSH、联网、读取评分器或其他研究。"
        ),
        "execution_instructions": "evaluate_task 参数为 task/candidate；"
        "diagnose_task 为 task/script/candidate"
        "（candidate 可为空字符串），路径相对工作区。"
        "使用 run_job(wait=true) 等待原作业，慢或断线不要重新提交。collect_bundle 参数为 {}；"
        "用 collect_job_output(destination='',as_candidate=true) 保存汇总候选，"
        "再 verify_candidate(phase='search',wait=true) 更新工作台。"
        "RSI 结束探索后，后台冻结历史最快正确源码，以 seed 137 复测；"
        "无需研究员消耗探索额度重复最终确认。",
        "verification_identity": {
            "adapter": "content",
            "phases": {
                phase: {
                    "protocol_id": manifest["protocol_id"],
                    "manifest_id": manifest["manifest_id"],
                }
                for phase in ("search", "final")
            },
        },
    }
    write_json(config_path, config.public(), immutable=True)
    return config.public()
