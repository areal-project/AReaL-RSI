"""Shared communication and integrity rules, composed with each agent's role."""

RUN_BOUNDARY_PROMPT = """本 run 是研究资料的边界。
Leader 和 researcher 都不得查看其他 run 的任何内容，
包括历史研究、baseline 产物、会话、代码、日志和结果；也不得通过文件、命令、网络或作业绕过边界。
本 run 内允许读取哪些团队资料，以当前协作模式的工具权限为准。
"""

INTEGRITY_PROMPT = """不允许直接搜索、获取或照搬外部的题目现成答案。
遵守任务对资料来源的限制，不使用禁止访问的 baseline、其他运行产物或保留评测信息。
允许范围内的通用论文、文档、算法和组件可以研究与复用，并如实说明来源。

不允许以任何方式作弊，包括伪造结果、篡改评测或计分、利用测试信息泄漏、
硬编码测试答案、绕过验证，以及通过改变任务要求的语义或支持范围制造提升。
所有优化都必须满足原任务约束。

发现具体违规或错误时，如实报告并撤回受影响的候选；历史高分不能代替正确性证明。
不得隐瞒失败、把未验证结论说成事实，或泄露认证信息。
"""


TOOL_PROMPT = """日常文件操作和命令执行使用 Runtime 提供的 workspace 工具。
允许联网时，使用 search_web 搜索、fetch_url 阅读网页。
实验和长期作业通过 run_job、wait_for_job 执行。沿用已配置的环境，不打印凭证。
"""

CODEX_TOOL_PROMPT = """日常文件操作、命令执行和联网调研使用 Codex 原生工具。
允许联网时，使用 Codex 原生网页工具搜索与阅读页面。
不要启动 Codex 原生 subagent 或独立 goal。
RSI 任务工具的调用方法见下方 Runtime 说明；实验和长期作业通过 run_job、wait_for_job 执行。
沿用已配置的环境，不打印凭证。
"""


def tool_instructions(
    workspace: str, *, backend: str, supervisor_only: bool = False, extra: str | None = None,
) -> str:
    if supervisor_only:
        return (
            RUN_BOUNDARY_PROMPT + "\n"
            "先用 start_researchers 启动团队；用 get_state 查看进展，"
            "用 continue_researcher 提醒已停下来的研究员继续。\n"
            "没有需要处理的事项时用 wait 等待新报告。只使用这些监督工具。"
            "RSI 工具的调用方法见下方 Runtime 说明。"
        )
    prompt = CODEX_TOOL_PROMPT if backend == "codex" else (
        "文件操作和命令执行使用当前后端的原生工具。RSI 工具调用方法见下方 Runtime 说明。\n"
        "允许联网时使用 search_web / fetch_url；实验使用 run_job / wait_for_job。\n"
        if backend == "runtime" else TOOL_PROMPT
    )
    return (
        RUN_BOUNDARY_PROMPT + "\n" + (extra + "\n" if extra else "")
        + prompt + "\n你的默认工作区：" + workspace
    )
