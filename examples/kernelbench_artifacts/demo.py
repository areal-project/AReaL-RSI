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
    value: dict[str, Any] = response["value"]
    return value


def prepare(
    root: Path,
    environment: Path,
    run_id: str,
    *,
    check: bool = False,
    task: str | None = None,
    max_agents: int = 3,
    max_wall_seconds: int | None = 3600,
    check_credentials: bool = True,
    web_research: bool | None = None,
    exploration_submissions: int | None = 10,
    name: str | None = None,
    time_limit_scope: str = "total",
    mode: str = "collaborative",
) -> dict[str, Any]:
    safe_name(run_id)
    if mode not in {"collaborative", "passk"}:
        raise ValueError("Unknown research mode")
    if mode == "passk" and (
        not task or max_wall_seconds is None or exploration_submissions is not None
    ):
        raise ValueError(
            "KernelBench passk requires one task, a wall limit and unlimited submissions"
        )
    if time_limit_scope not in {"total", "exploration"}:
        raise ValueError("time_limit_scope must be total or exploration")
    if mode == "passk":
        time_limit_scope = "exploration"
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
        existing: dict[str, Any] = json.loads(config_path.read_text())
        if existing.get("mode", "collaborative") != mode:
            raise ValueError("此研究已有不同的协作方式；请新建研究")
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
    if mode == "passk":
        manifest["mode"] = mode
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
    rules = (HERE / ("PASSK_RULES.md" if mode == "passk" else "RULES.md")).read_text()
    budget_policy = (
        "GPU 提交次数不限，可以按需要评测、调试和比较参数，无需逐次确认。所有作业都会保留记录。"
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
            parameter_descriptions={
                "task": "题目标识，可选值：" + "、".join(demo["tasks"]) + "；不是文件路径。",
                "candidate": "自包含产物文件，相对自己工作区的路径，例如 model_new.py。",
            },
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
            parameter_descriptions={
                "task": "题目标识，可选值：" + "、".join(demo["tasks"]) + "；不是文件路径。",
                "script": "自包含诊断脚本，相对自己工作区的文件路径。",
                "candidate": "供诊断导入的产物文件，相对自己工作区的路径；无需产物时填空字符串。",
            },
        ),
        ApprovedCommand(
            "collect_bundle", (*common, "collect", *fixed, "--output", "{output}"), ("output",)
        ),
    )
    time_policy = "本次不设时间上限。"
    if task and max_wall_seconds is not None:
        duration = (
            f"{max_wall_seconds // 60} 分钟"
            if max_wall_seconds % 60 == 0
            else f"{max_wall_seconds} 秒"
        )
        time_policy = (
            f"探索时间最多 {duration}，最终独立复测另计。"
            if time_limit_scope == "exploration"
            else f"研究总时间最多 {duration}，包含最终独立复测。"
        )
    config.brief = TaskBrief(
        title if name else title + " · " + config.model,
        "请优化 "
        + "、".join(demo["tasks"])
        + " 的完整 ModelNew，在保持原始语义和正确性的前提下，尽可能提高整题加速比。"
        + ("没有固定的达标分数。" if task else "")
        + "\n\n题目在工作区的 tasks/<题号>/reference.py，具体要求见 RULES.md。"
        "从本题公开材料开始研究，不读取其他研究或 baseline 的候选源码。"
        + ("先理解任务并主动联网调研，" if allow_web else "先理解任务并研究已有材料，")
        + "再制定团队研究计划，组织研究员并行实施；"
        "过程中遇到需要进一步了解的问题，继续有针对性地查阅允许范围内的资料。"
        + "\n\n"
        + time_policy
        + budget_policy
        + f"同时最多 {demo['max_agents']} 名研究员，等待中的研究员也占名额；"
        "停止后可以创建新的研究员，累计人数不限。由 RSI 决定研究方向和分工。"
        "\n\n按研究进展决定何时结束，不必用满预算。结束时用 stop_run 说明结果与原因，"
        "系统会选取仍有效的最佳正式正确产物，冻结后独立复测。最终反馈不能再用于修改候选。",
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
            min(180, max_wall_seconds / 10)
            if task and time_limit_scope == "total" and max_wall_seconds is not None
            else 0
        ),
        "allow_web_access": allow_web,
        "agent_tool_instructions": (
            "请先阅读工作区的 RULES.md 和公开题目。只在自己的工作区操作，"
            "通过研究工具共享本次研究的资料和产物。GPU 实验使用已配置的作业工具，"
            "不要直接 SSH，也不要读取评分器或其他研究。\n" + web_policy
        ),
        "execution_instructions": "用 evaluate_task 评测产物，传入 task 和 candidate；"
        "用 diagnose_task 运行诊断，传入 task、script 和 candidate，"
        "不需要候选时将 candidate 留为空字符串。文件路径均相对于自己的工作区。\n\n"
        "远端只接收指定的文件，不会上传整个目录，因此候选和诊断脚本需要自包含。"
        "候选在远端名为 model_new.py，诊断脚本名为 _rsi_diagnostic.py，"
        "并提供题目的 reference.py；诊断中用 import model_new 导入候选。\n\n"
        "用 run_job(wait=true) 等待结果；作业慢或连接中断时，先查看原作业，避免重复提交。"
        "collect_bundle 不需要参数，传入 {} 即可。"
        + (
            "evaluate_task 会自动保存正确产物和正式成绩并更新工作台，不会额外提交 GPU 作业；"
            "无需再手动 collect_bundle、保存候选或刷新成绩。"
            if task
            else "用 collect_job_output(destination='',as_candidate=true) 保存汇总候选，"
            "再 verify_candidate(phase='search',wait=true) 更新工作台。"
        )
        + "\n\n探索结束后，系统会冻结仍有效的历史最佳正式正确源码，以 seed 137 独立复测。"
        "最终确认由系统完成，不需要研究员重复提交。",
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
    if mode == "passk":
        config.mode = mode
        config.brief.limits.max_jobs = max_agents
        config.commands = tuple(command for command in commands if command.name != "collect_bundle")
        config.brief.instructions = (
            f"请优化 {task} 的完整 ModelNew，在保持原始语义和正确性的前提下，尽可能提高整题加速比。"
            "没有固定的达标分数。\n\n"
            f"公开题目在 tasks/{task}/reference.py，评测要求见 RULES.md。"
            "每个研究员从相同材料开始，独立完成调研、实现、实验和改进，自行决定研究方向。"
            "开始时主动联网调研，后续按需要继续查阅允许范围内的资料。\n\n"
            f"{time_policy}{budget_policy}固定启动 {max_agents} 名研究员，不替换或追加尝试。"
            "可以向 RSI 简要报告进展；RSI 只监督持续探索，不提供建议，也不传递其他尝试的信息。"
            "探索到时由系统停止，选取仍有效的历史最佳正式正确产物独立复测。"
        )
        config.metadata["agent_tool_instructions"] = (
            "先阅读自己的 RULES.md 和公开题目。仅操作自己的工作区；"
            "GPU 实验使用登记的作业工具，不直接 SSH，不读取其他研究或评分器。\n" + web_policy
        )
        config.metadata["execution_instructions"] = (
            "用 evaluate_task 评测产物，参数为 task、candidate；"
            "用 diagnose_task 运行诊断，参数为 task、script、candidate，不需候选时 candidate 留空。"
            "路径均相对于自己的工作区，候选和诊断脚本需要自包含。"
            "远端文件名为 model_new.py、_rsi_diagnostic.py，并提供 reference.py。\n\n"
            "用 run_job(wait=true) 等待作业；连接中断时先查询原作业，避免重复提交。"
            "evaluate_task 自动保存本次正确产物和正式成绩，无需额外收集或再次评测。"
            "系统在探索结束后冻结历史最佳有效候选并独立复测，研究员不重复提交最终确认。"
        )
        config.metadata["finalization_reserve_seconds"] = 0
        config.__post_init__()
    write_json(config_path, config.public(), immutable=True)
    return config.public()
