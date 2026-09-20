"""Prepare a pass@k KernelBench task without reading any previous research run."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from areal_rsi.api.records import EvolutionTarget
from areal_rsi.api.research import ResearchLimits, TaskBrief, VerificationContract
from areal_rsi.infra.storage.files import safe_name, write_json
from areal_rsi.infra.coordination.policies.collaboration import DEFAULT_MODULES, selected_modules
from areal_rsi.infra.coordination.config import ResearchConfig
from areal_rsi.infra.execution import ApprovedCommand

HERE = Path(__file__).resolve().parent


def control_request(environment: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    command = [environment.get("python", "/opt/.venv/bin/python"), "-B"]
    if payload["operation"] in {"calibrate", "finish"}:
        command.append(payload["manifest"]["remote_control"])
    else:
        command += ["-c", (HERE / "control.py").read_text()]
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
    task: str = "L3P10",
    max_agents: int = 4,
    max_wall_seconds: int | None = 3600,
    check_credentials: bool = True,
    web_research: bool | None = None,
    exploration_submissions: int | None = None,
    name: str | None = None,
    time_limit_scope: str = "exploration",
    mode: str = "passk",
    collaboration_modules: tuple[str, ...] = DEFAULT_MODULES,
    max_effective_seconds: int | None = None,
) -> dict[str, Any]:
    safe_name(run_id)
    collaboration_modules = selected_modules(mode, collaboration_modules)
    guided = "leader_guidance" in collaboration_modules
    if not re.fullmatch(r"L[123]P[1-9][0-9]*", task):
        raise ValueError("题号格式应为 L3P10")
    if max_wall_seconds is None and max_effective_seconds is None:
        raise ValueError("KernelBench passk requires a wall limit or effective-time limit")
    if exploration_submissions is not None:
        raise ValueError("KernelBench passk requires unlimited submissions")
    if time_limit_scope != "exploration":
        raise ValueError("KernelBench passk limits exploration; final confirmation runs afterward")
    ResearchLimits(
        max_agents=max_agents,
        max_wall_seconds=max_wall_seconds,
        max_effective_seconds=max_effective_seconds,
    )
    demo = json.loads((HERE / "demo.json").read_text())
    demo["exploration_submissions"] = None
    demo["tasks"] = {task: demo["tasks"].get(task, {})}
    demo.update(max_agents=max_agents, target=None, metric="speedup")
    title = name or f"KernelBench {task} 单题研究"
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
        if (
            selected_modules(mode, existing.get("collaboration_modules", ()))
            != collaboration_modules
        ):
            raise ValueError("此研究已有不同的协作模块；请新建研究，不能复用其他版本的配置")
        previous_demo = json.loads((setup / "demo.json").read_text())
        if existing["metadata"].get("time_limit_scope", "total") != time_limit_scope:
            raise ValueError("此研究已有不同的时间口径；请新建研究")
        if previous_demo["exploration_submissions"] != exploration_submissions:
            raise ValueError("此研究已有不同的提交限制；请新建研究，不能改变已登记实验条件")
        if web_research is not None and (
            existing["metadata"].get("allow_web_access", True) != web_research
        ):
            raise ValueError("此研究已有不同的联网设置；请新建研究，不能改变已登记实验条件")
        if (
            list(json.loads((setup / "demo.json").read_text())["tasks"]) != [task]
            or existing["brief"]["limits"]["max_agents"] != max_agents
            or existing["brief"]["limits"]["max_wall_seconds"] != max_wall_seconds
            or existing["brief"]["limits"].get("max_effective_seconds") != max_effective_seconds
        ):
            raise ValueError("此研究已有不同的单题配置；请新建研究，不能重置提交额度")
        prepare(
            root,
            environment,
            run_id,
            check=True,
            task=task,
            mode=mode,
            collaboration_modules=collaboration_modules,
            exploration_submissions=exploration_submissions,
        )
        manifest = json.loads((setup / "gpuq-manifest.json").read_text())
        if manifest.get("job_isolation") != "filesystem-network-v1":
            raise ValueError("旧研究未登记远端隔离；请新建研究，不修改历史实验。")
        return existing
    allow_web = True if web_research is None else web_research
    payload = {
        "operation": "check" if check else "prepare",
        "run_id": run_id,
        "demo": demo,
        "environment": env,
        "remote_source": (HERE / "remote.py").read_text(),
        "control_source": (HERE / "control.py").read_text(),
        "scoring_source": (HERE / "scoring.py").read_text(),
        "queue_sandbox_sha256": hashlib.sha256(
            (HERE / "queue_sandbox.py").read_bytes()
        ).hexdigest(),
    }
    prepared = control_request(env, payload)
    if check:
        return prepared
    manifest = prepared["manifest"]
    if manifest.get("search_scoring"):
        while True:
            result = control_request(env, {"operation": "calibrate", "manifest": manifest})
            if result["status"] == "completed":
                manifest = result["manifest"]
                break
            time.sleep(2)
    manifest["diagnostic_precision"] = "strict-fp32-v1"
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
            manifest["tasks"][case].get("reference_calibration")
            or demo["tasks"][case].get("reference_baseline", {"available": False}),
            immutable=True,
        )
        demo["tasks"][case]["sha256"] = manifest["tasks"][case]["reference_sha256"]
    write_json(setup / "demo.json", demo, immutable=True)
    rules = (HERE / "PASSK_RULES.md").read_text()
    if guided:
        rules = rules.replace(
            "可以向 RSI 报告进展，但不等待技术指导，不与其他研究员交换信息。",
            "向 RSI 报告关键进展和困难，报告后继续研究。RSI 可以定向分享原始证据和建议；"
            "需要细节时可通过研究工具查阅本 run 同事的报告、轨迹和候选代码，"
            "在自己的工作区实现并验证。不得读取其他 run 的任何资料。",
        )
        rules = rules.replace(
            "不读取其他研究或 baseline 的候选源码。",
            "本 run 内可以参考和复用同事的代码；禁止读取其他 run 的任何内容或外部 baseline。",
        )
        rules = rules.replace(
            "不读取评分器、SDK 内部、父目录、其他会话或旧研究。",
            "不通过文件系统读取评分器、SDK 内部、父目录或其他会话。"
            "本 run 同事的公开资料通过研究工具查阅。",
        )
    budget_policy = (
        "GPU 提交次数不限，每次根据反馈选择下一项实验。"
        "与 goal baseline 保持一致：不使用 triton.autotune、批量配置扫描或自动调参循环，"
        "诊断脚本也遵守这一要求。所有作业都会保留记录。"
    )
    rules += "\n本次提交规则：" + budget_policy + "\n"
    web_policy = (
        "本次允许联网调研通用方法、官方文档和论文；禁止搜索题目答案、"
        "获取外部现成答案，或读取其他 run 的任何内容及外部 baseline。"
        if allow_web
        else "本次禁止联网搜索和网页读取；仅使用本研究提供的材料进行调研。"
    )
    rules += "\n" + web_policy + "\n"
    if guided:
        rules += "\n本 run 团队自己的研究产物允许相互参考和复用，采纳后仍需自行验证。\n"
    (seed / "RULES.md").write_text(rules)
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
            queue_timing_file="remote-job.json",
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
            queue_timing_file="remote-job.json",
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
    if max_wall_seconds is not None:
        duration = (
            f"{max_wall_seconds // 60} 分钟"
            if max_wall_seconds % 60 == 0
            else f"{max_wall_seconds} 秒"
        )
        time_policy = f"探索时间最多 {duration}，最终独立复测另计。"
    if max_effective_seconds is not None:
        time_policy = (
            f"每名研究员最多探索 {max_effective_seconds / 60:g} 分钟有效时间，"
            "各自到时停止，最终独立复测另计。"
            "有效时间扣除 API 故障重试和 GPU 排队等待，重叠只扣一次；"
            "正常推理、编译和 GPU 执行计时。"
        )
    config.brief = TaskBrief(
        title if name else title + " · " + config.model,
        "",  # Complete task instructions are assembled below for the selected modules.
        ResearchLimits(
            max_agents=demo["max_agents"],
            max_nodes=None,
            max_total_tokens=None,
            max_wall_seconds=max_wall_seconds,
            max_effective_seconds=max_effective_seconds,
            max_verify_calls=None,
            max_jobs=None,
        ),
        VerificationContract(
            demo.get("metric", "fast_1"),
            target=demo["target"],
            description=(
                "探索加速比为固定 reference 耗时/候选耗时；"
                "冻结源码后以 seed 137 独立复测，最终分数使用当次 reference 耗时"
            ),
            identity={"require_final_confirmation": True},
        ),
    )
    config.evolution_target = EvolutionTarget.ARTIFACT
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
        "allow_web_access": allow_web,
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
    config.mode = mode
    config.collaboration_modules = collaboration_modules
    config.brief.limits.max_jobs = max_agents
    config.commands = tuple(command for command in commands if command.name != "collect_bundle")
    config.brief.instructions = (
        f"请优化 {task} 的完整 ModelNew，在保持原始语义和正确性的前提下，尽可能提高整题加速比。"
        "没有固定的达标分数。\n\n"
        f"公开题目在 tasks/{task}/reference.py，评测要求见 RULES.md。"
        "每个研究员从相同材料开始，独立完成调研、实现、实验和改进，自行决定研究方向。"
        "开始时主动联网调研，后续按需要继续查阅允许范围内的资料。\n\n"
        f"{time_policy}{budget_policy}固定启动 {max_agents} 名研究员，不替换或追加尝试。"
        + (
            "向 RSI 报告关键进展和困难，报告后继续研究。"
            "RSI 综合团队进展、核查原始证据并提出明确的非阻塞指导；"
            "研究员需要时可以查阅本 run 同事的资料，"
            "仍自行实现和验证整题。"
            if guided
            else "可以向 RSI 简要报告进展；RSI 只监督持续探索，不提供建议，也不传递其他尝试的信息。"
        )
        + "探索加速比使用启动前校准的固定 reference，见 baseline.json。"
        "探索到时由系统停止，只对正式成绩最好的有效候选做一次独立复测。"
    )
    config.metadata["agent_tool_instructions"] = (
        "先阅读自己的 RULES.md 和公开题目。仅操作自己的工作区；"
        "GPU 实验使用登记的作业工具，不直接 SSH，不读取其他 run 的任何内容或评分器。\n"
        + web_policy
        + ("\n本 run 团队产物允许通过研究工具查阅和复用，仍需自行验证。" if guided else "")
    )
    config.metadata["execution_instructions"] = (
        "用 evaluate_task 评测产物，参数为 task、candidate；"
        "用 diagnose_task 运行诊断，参数为 task、script、candidate，不需候选时 candidate 留空。"
        "路径均相对于自己的工作区，候选和诊断脚本需要自包含。"
        "远端文件名为 model_new.py、_rsi_diagnostic.py，并提供 reference.py。\n\n"
        "max_jobs 只限制并发作业数量，不限制累计提交次数。\n"
        "用 run_job(wait=true) 等待作业；连接中断时先查询原作业，避免重复提交。"
        "evaluate_task 自动保存本次正确产物和正式成绩，无需额外收集或再次评测。"
        "系统在探索结束后按正式成绩选优并独立复测，研究员不重复提交最终确认。"
    )
    config.metadata["finalization_reserve_seconds"] = 0
    config.metadata["budget_clock"] = (
        "baseline-active-v1" if max_effective_seconds is not None else "wall"
    )
    config.metadata["comparison_protocol"] = {
        "evaluation_policy": "strict-fp32-v1",
        "diagnostic_precision": "strict-fp32-v1",
        "batched_tuning": False,
        "selection_metric": "search_score",
        "budget_clock": config.metadata["budget_clock"],
        "final_confirmation_outside_exploration": True,
    }
    config.__post_init__()
    write_json(config_path, config.public(), immutable=True)
    return config.public()
