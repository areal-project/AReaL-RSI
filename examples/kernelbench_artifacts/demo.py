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
from areal_rsi.base.storage import safe_name, write_json
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


def prepare(
    root: Path,
    environment: Path,
    run_id: str,
    *,
    check: bool = False,
    task: str | None = None,
    max_agents: int = 3,
    max_wall_seconds: int = 3600,
    check_credentials: bool = True,
    web_research: bool | None = None,
    exploration_submissions: int | None = 10,
    name: str | None = None,
    time_limit_scope: str = "total",
) -> dict[str, Any]:
    safe_name(run_id)
    if time_limit_scope not in {"total", "exploration"}:
        raise ValueError("time_limit_scope must be total or exploration")
    if exploration_submissions is not None and (
        type(exploration_submissions) is not int or exploration_submissions < 1
    ):
        raise ValueError("探索提交次数应为正整数或 null（不限次数）")
    if task:
        ResearchLimits(max_agents=max_agents, max_wall_seconds=max_wall_seconds)
    demo = json.loads((HERE / "demo.json").read_text())
    demo["exploration_submissions"] = exploration_submissions
    if task:
        import re

        if not re.fullmatch(r"L[123]P[1-9][0-9]*", task):
            raise ValueError("题号格式应为 L3P10")
        demo["tasks"] = {task: demo["tasks"].get(task, {})}
        demo.update(max_agents=max_agents, target=None, metric="speedup")
    title = name or (f"KernelBench {task} 单题研究" if task else "KernelBench 八题研究")
    env = json.loads(environment.read_text())
    config = ResearchConfig(
        TaskBrief(title, ""),
        model=demo["model"],
        reasoning_effort=demo["reasoning_effort"],
        base_url=demo["base_url"],
    )
    config.pin_model_settings()
    if not check and check_credentials:
        config.runtime()  # Check local credentials before registering any remote tasks.
    setup = root / "runs" / run_id / "setup"
    config_path = setup / "research.json"
    if not check and config_path.exists():
        existing = json.loads(config_path.read_text())
        previous_demo = json.loads((setup / "demo.json").read_text())
        if existing["metadata"].get("time_limit_scope", "total") != time_limit_scope:
            raise ValueError("此研究已有不同的时间口径；请新建研究")
        if previous_demo["exploration_submissions"] != exploration_submissions:
            raise ValueError("此研究已有不同的提交限制；请新建研究，不能改变已登记实验条件")
        if web_research is not None and (
            existing["metadata"].get("allow_web_access", True) != web_research
        ):
            raise ValueError("此研究已有不同的联网设置；请新建研究，不能改变已登记实验条件")
        if task and (
            list(json.loads((setup / "demo.json").read_text())["tasks"]) != [task]
            or existing["brief"]["limits"]["max_agents"] != max_agents
            or existing["brief"]["limits"]["max_wall_seconds"] != max_wall_seconds
        ):
            raise ValueError("此研究已有不同的单题配置；请新建研究，不能重置提交额度")
        return existing
    allow_web = True if web_research is None else web_research
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
    seed = setup / "public-inputs"
    for case, ref in prepared["references"].items():
        source = base64.b64decode(ref["source"], validate=True)
        if hashlib.sha256(source).hexdigest() != manifest["tasks"][case]["reference_sha256"]:
            raise ValueError("公开题目摘要不匹配")
        folder = seed / "tasks" / case
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "reference.py").write_bytes(source)
        write_json(
            folder / "baseline.json",
            demo["tasks"][case].get("reference_baseline", {"available": False}),
            immutable=True,
        )
        demo["tasks"][case]["sha256"] = manifest["tasks"][case]["reference_sha256"]
    write_json(setup / "demo.json", demo, immutable=True)
    rules = (HERE / "RULES.md").read_text()
    budget_policy = (
        "GPU 提交次数不限，包括正式评测、验证、诊断、失败和参数比较；"
        "可按需调试和调参，无需逐次请求许可。所有作业保留记录。"
        if exploration_submissions is None
        else f"每题最多 {exploration_submissions} 次探索 GPU 提交，包含诊断和失败；"
        "各研究员共享额度，最终另由程序独立复测一次。"
        "禁止 triton.autotune、批量候选或配置搜索，包括诊断中的调参循环。"
    )
    rules += "\n本次提交规则：" + budget_policy + "\n"
    web_policy = (
        "本次允许联网调研通用方法、官方文档和论文；禁止搜索题目答案、"
        "获取或复制本题现成的完整实现，以及读取其他研究或 baseline 的候选源码。"
        if allow_web
        else "本次禁止联网搜索和网页读取；仅使用本研究提供的材料进行调研。"
    )
    rules += "\n" + web_policy + "\n"
    (seed / "RULES.md").write_text(rules.replace("八题研究", f"{task} 单题研究") if task else rules)
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
            auto_verify_output="candidate" if task else None,
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
        title if name else title + " · " + config.model,
        "研究并优化 "
        + "、".join(demo["tasks"])
        + " 的完整 ModelNew。先保证正确性，再提高整题加速比。"
        "阅读自己工作区 RULES.md 和 tasks/<题号>/reference.py；不读取其他 run 或历史解法。"
        f"同时最多 {demo['max_agents']} 名研究员（含等待），停止后名额可复用，累计创建数不限；"
        "RSI 自主决定方向、分工和阶段汇报点；"
        "实现和实验交给研究员。"
        + budget_policy
        + "研究结束由程序冻结候选并进行独立复测，最终反馈不再用于修改。"
        "达到目标或判断无法继续改善时，用 stop_run 结束探索，后台自动收尾。"
        "不用重复请求已给定的环境、资源和目标。"
        + (
            f"探索最长 {max_wall_seconds} 秒，最终复测另计；这是上限，不是必须用满。"
            if task and time_limit_scope == "exploration"
            else f"整项研究最长 {max_wall_seconds} 秒，包含最终复测；这是上限，不是必须用满。"
            if task
            else ""
        )
        + (
            "目标是正确前提下尽可能高的加速比，没有固定达标阈值。"
            "只做有依据的改进，不凑满时间或提交次数；提前结束也会自动复测。"
            "并行方向和阶段沟通由 RSI 决定。"
            if task
            else "不另设总时间或模型调用上限。"
        ),
        ResearchLimits(
            max_agents=demo["max_agents"],
            max_nodes=None,
            max_model_calls=None,
            max_total_tokens=None,
            max_wall_seconds=max_wall_seconds if task else None,
            max_verify_calls=None,
            max_jobs=None,
        ),
        VerificationContract(
            demo.get("metric", "fast_1"),
            target=demo["target"],
            description=(
                "正式正确的完整 ModelNew 加速比（参考耗时/候选耗时）；冻结源码以 seed 137 独立复测"
                if task
                else "八题中正式正确且 speedup > 1 的比例；冻结源码以 seed 137 独立复测"
            ),
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
        "time_limit_scope": time_limit_scope,
        "finalization_reserve_seconds": (
            min(180, max_wall_seconds / 10) if task and time_limit_scope == "total" else 0
        ),
        "allow_web_access": allow_web,
        "agent_tool_instructions": (
            "遵守 RULES.md。只使用自己的本地工作区，GPU 仅经登记作业工具调用；"
            "禁止直接 SSH、读取评分器或其他研究。"
            + web_policy
            + ("联网调研使用 search_web 和 fetch_url，便于归档来源。" if allow_web else "")
        ),
        "execution_instructions": "evaluate_task 参数为 task/candidate；"
        "diagnose_task 为 task/script/candidate"
        "（candidate 可为空字符串），路径相对工作区。"
        "使用 run_job(wait=true) 等待原作业，慢或断线不要重新提交。collect_bundle 参数为 {}；"
        + (
            "evaluate_task 会自动归档本次正确产物、调用只读验证器并更新工作台，"
            "不会追加 GPU 提交；无需手动 collect_bundle、保存候选或刷新分数。"
            if task
            else "用 collect_job_output(destination='',as_candidate=true) 保存汇总候选，"
            "再 verify_candidate(phase='search',wait=true) 更新工作台。"
        )
        + "RSI 结束探索后，后台冻结历史最快正确源码，以 seed 137 复测；"
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
