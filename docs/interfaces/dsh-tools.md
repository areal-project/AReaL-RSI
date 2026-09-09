# 工作台的 DSH 基础工具

`ResponsesRuntime` 继续拥有模型请求、预算、compact、历史及消息恢复。`AgentTools` 将 DSH 工具注册到 `ResearchActions`，每个 Agent 首次使用时启动一个只加载工具的 Cordis 进程。该进程没有 LLM provider、Agent loop 或子 Agent 插件；它在没有调用时阻塞等待 stdin，不消耗模型 token。

## 能力与归属

三个角色共有：DSH `bash / read / write / edit / glob / grep`，以及现有 Python 联网工具 `search_web / fetch_url`。DSH 固定为 `0.1.2-rc.1`，只使用其公开的 `ctx.tools.schemas()` 和 `ctx.tools.execute()`；见[上游工具运行时说明](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/tools/README.md)。角色专属的调度、报告、作业和验证工具保持各自职责。旧工具名继续注册，已有会话中的调用结果无需迁移。

```text
RSI_DATA_ROOT/
  dsh-tools/<lock摘要>/               # 可重新安装的 Node 依赖缓存
  runs/<run_id>/
    actions/                         # 原有工具调用审计与结果
    agents/<agent_id>/
      sessions/                      # 原 Responses 会话，位置和身份不变
      workspace/                     # 相对路径的默认目录
      tools/
        calls/<调用ID摘要>.json       # started/completed 回执
        worker.log                   # 工具进程诊断
        tmp/                         # DSH 的临时输出与溢出日志
```

每个 Agent 具有独立工具进程和文件观察记录。DSH 的 observation policy 要求修改已有文件前读取，并拒绝覆盖读取后发生的外部修改。工具进程重启或执行环境改变后需要重新读取相关文件，模型上下文仍保持连续。

工具 schemas 从固定组合生成到 `resources/dsh_tools/schemas.json`，使未调用工具的会话无需安装 Node 依赖。启动工具进程时比对真实注册结果，拒绝不匹配的安装。适配层只翻译说明，保留上游参数及可选语义（Responses `strict=false`）。更新插件或组合时需重新生成 schemas 并运行真实 DSH 测试。

## 命令生命周期

Python 按 Agent 串行传入 JSON 请求。Node 在进入 DSH 执行流水线前写入持久回执，完成后原子保存结果。重复的同一调用返回原结果；同 ID 不同参数被拒绝；只有开始记录而没有结果时返回 `OUTCOME_UNKNOWN`。这里不声称操作和日志能跨进程原子提交，结果不明时必须检查实际状态。

每次 bash 使用新 shell，默认 30 秒、最多 60 秒，输出有限长，溢出保存在 run 内。取消或进程 stdin 关闭会中断当前工具并清理 DSH 管理的进程树。异步实验通过已有 `run_job / wait_for_job` 持久作业接口执行；此组合不启用 DSH 后台作业，避免出现两套实验调度与恢复机制。

工具进程不继承模型密钥、令牌或自定义研究凭证变量；保留 PATH、HOME 和 SSH_AUTH_SOCK，使本机工具和已有 SSH 认证可用。它以本机用户权限运行，并非不可信代码沙箱。默认目录与 prompt 中的范围约束不能代替系统权限隔离；私有评测和不可信候选须使用已有容器或独立验证入口。通用终端输出不进入正式分数表。

## 安装与验证

首次调用自动安装；也可提前准备，避免首个检查等待下载：

```bash
uv run python -c 'from pathlib import Path; from areal_rsi.modules.dsh_tools import ensure_tools; print(ensure_tools(Path(".rsi/local")))'
```

依赖目录由 lockfile 摘要区分，以文件锁保护并发安装，使用 `npm ci --ignore-scripts`。Python wheel 包含 Node worker、package/lockfile 及 schemas，部署不依赖源码工作目录。

运行真实 DSH 集成测试（将安装命令返回的目录填入变量）：

```bash
RSI_DSH_TOOLS_TEST_INSTALLATION=/absolute/path/to/dsh-tools/<digest> uv run pytest tests/test_dsh_tools.py
```

没有设置该变量时，默认测试不下载依赖，跳过需要 Node 的集成用例。覆盖文件读写和搜索、文件改动保护、Agent 工作区区分、已完成调用的重放、结果不明的恢复、超时与取消、环境更新，以及旧对话重启后使用新工具且等待期间不调用模型。
