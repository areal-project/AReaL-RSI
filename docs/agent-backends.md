# Agent backends

ResearchConfig 的 `backend` 支持 `standard`（默认）、`codex` 和 `runtime`。
两者均通过仓库原有的 `PackageAgentRuntime` 使用 AReaL Agent Runtime 的公开
`build_composition` / Application API。RSI 不再维护独立的 Codex App Server 客户端、
请求代理或研究模型执行循环。旧 `ResponsesRuntime` 及其自建请求、重试、压缩循环
已删除。连接配置和运行时接口分别位于 `research_settings.py`、`agent_runtime.py`；
`rsi preflight` 直接验证工作台实际使用的包运行时。

| 选项 | Runtime 适配器 | 文件与命令 | 联网 |
| --- | --- | --- | --- |
| standard | dsh | Runtime 的 workspace tools | RSI search_web / fetch_url |
| codex | codex-cli | Codex 原生工具 | Codex 原生 web search |

两个后端使用同一份研究配置中的模型、API 地址和密钥。DSH 通过原生 provider 配置使用
Responses 协议，并明确声明模型的推理强度。Codex 使用锁定版本的 CLI，由 Runtime 管理
App Server；原生搜索保留，原生 subagent 和 goal 禁用。无需 OpenAI Codex Python SDK。

## 其他 Runtime 后端

`examples/runtime/` 保留 Claude Code、Claude Agent SDK、Anthropic、Hermes、
remote、entrypoint 和混合角色示例。这些是适配器配置，不是另一套编排器；仍由 ResearchApp
执行研究。示例不代表所有后端已完成当前版本的端到端验收。

```bash
export RSI_RUNTIME_CONFIG="$PWD/examples/runtime/hermes.json"
export RSI_BACKEND=runtime
uv run --env-file .env rsi preflight --backend runtime
```

也可在 ResearchConfig 写入 `backend: "runtime"` 和 `runtime_config` 对象。
配置文件在创建研究时保存快照，之后修改文件不会改变该研究。
`roles` 支持 `interaction-agent`、`rsi-agent`、`research-agent`；未指定的角色用 `default`。
模型连接仍共用研究配置；不同协议的后端需要模型端点支持对应协议。
Hermes 本次验证版本为 0.17.0（commit `2f5950a83a66d2b91918bb78e6d1b60f3f48b938`），
Tirith 为 0.4.1。需要安装源码、Python 环境及真实 Tirith 扫描器，更新示例中的路径；
通过原生 terminal 调用 `rsi-tools`。remote/entrypoint 需要管理员部署相应服务或插件。
配置仅供受信操作员设置，不能内嵌 API key；使用研究凭证或 `authTokenEnv`。

## RSI 任务工具

DSH 通过 Runtime 的 ToolProvider 插件调用 RSI 工具。当前版本的 Runtime Codex 适配器
不能接入 Application ToolSpec，因此 Codex 使用原生命令工具调用沙箱内的 RSI 工具客户端。
客户端提供工具列表和参数说明，通过每个会话独有的能力令牌访问父进程。
父进程校验工具、参数及当前 agent 权限，沿用已有的动作幂等记录、作业和验证流程。
工具调用的去重身份由会话目录绝对路径、RSI 逻辑消息 ID 和原生调用 ID 确定。
不同消息可使用相同的原生调用 ID；同一消息在进程重启或显式重试后仍使用原身份，
参数不一致的重试仍会被拒绝。该规则统一生效，不兼容旧会话中未分域的调用记录。
不能直接以原生 Run ID 分域，因为它会在恢复时改变。

已有协作模式仍使用持久化收件箱。执行中，在 RSI 任务工具返回时附带待收消息；正式报告
后继续等待。当前包的 Codex 适配器没有运行中 steer 能力，因此不能承诺在任意原生工具
边界立即注入消息。隔离 pass@k 不依赖这项能力，也不开放跨 agent 消息。

## 会话、停止和记录

默认 `sandbox` 模式下，每个会话有独立工作区、原生运行目录和 macOS
`sandbox-exec` 子进程，隐藏评测数据和其他研究员工作区不向子进程开放。

可信 Linux 服务器可在启动工作台前显式设置：

```dotenv
RSI_RUNTIME_ISOLATION=linux-unrestricted
```

此模式适用于 standard 和 codex 后端。它直接启动独立 Python 子进程，使用 bash，
保留每个会话的 HOME、TMPDIR、日志、工具回执及进程组取消机制，并传递网络代理变量。
内置 workspace 工具同时允许绝对路径（含符号链接）、工作区外的仓库/训练池，
以及 bash、/bin/bash 和任意可执行文件；相对路径仍以当前 Agent 工作区为起点。
命令保留 60 秒超时和输出上限，长时间训练应由现有脚本异步提交。
它不提供文件、进程或网络隔离；Agent 拥有工作台启动用户的权限，root 启动即拥有 root
权限，也能访问该用户可读的其他研究目录、凭据文件和隐藏评测数据。仅用于可信研究，
不能用于要求答案隔离的评测。环境变量默认是 `sandbox`，Linux 不会自动降级。
修改后须重启工作台；这是操作员环境配置，不能由模型的研究配置启用。
RSI 管理任务和正式验证，Runtime 管理推理、工具、
上下文压缩及供应商重试；不会把 Runtime 当成外壳再运行自定义 harness。

完整角色 prompt 绑定在 Runtime 的 AgentEnvironment 中。每个 RSI 回合提供当前任务状态，
后续回合使用原生会话续接；压缩由各自 harness 负责。进程重启时重放已提交的 Runtime
journal，恢复环境和原生 session binding；不把旧 Responses/Codex 自建会话格式当作新会话。
新的模型配置会在研究开始前创建新会话并保留已记录的用户对话。
`context_window_tokens` 显式指定时传给两个后端；省略时 standard 使用 RSI 的模型窗口默认值，
Codex 使用原生默认窗口。`max_output_tokens` 仅支持 standard 自定义；Codex 沿用原生
输出限制，传入非默认值会被拒绝。

新 `ResearchConfig` 不再包含 `compact_keep_recent_tokens`、`max_model_calls_per_turn`、
`retry_initial_seconds` 和 `retry_max_seconds`。配置只接受当前字段，旧字段会明确报错，
不自动迁移历史配置。这些设置之前未传入原生后端，不能继续把自定义值当作已经生效。
重试和压缩由原生 harness 管理，研究的时限和 token 停止条件使用 `brief.limits`。

Runtime journal 是执行事实来源，包含原生事件、工具执行和 usage。工作台汇总供应商实际
报告的 token，并单独记录 runtime turns。当前 Runtime 的两个适配器没有逐次 model-call
数据；runtime turn 不等于一次模型请求。原先请求代理的逐请求预算预留和请求文件不再生成，
不能用该字段进行请求数或精确费用比较。`ResearchLimits.max_model_calls` 保留为可空兼容字段，默认 `null`；
当前原生运行时无法执行该上限，非空值会明确报错，需求工具和工作台不提供这个设置。
token 限制按
已收到的 usage 停止，不代表供应商账单的严格上限。中断请求可能没有完整 usage。
工具参数定义改变后不自动迁移旧草稿会话，需要新建研究。

停止会取消原生执行和受管作业；探索时限使用持久化的启动时间，不因恢复会话而重置。
正式复测沿用原有 finalizer。具体实验约定见 [pass@k](passk.md)。

### 需求更新工具

Interaction 的 `update_brief` 使用结构化的 `objective`、`instructions`、`limits` 和
`verification` 参数，按当前协作模式生成定义。协作模式提供当前支持的预算字段；pass@k
隐藏 token 总量、验证次数和累计节点数上限，由后端将这些字段保持为
`null`。工具仍提供研究员数量、研究时限、并发作业数、GPU 和可修改路径。
服务端使用同一份 schema 校验，并在写入任何需求记录前验证完整研究配置。
旧的 `brief_json` 参数不再接受；已完成工具调用的回放记录保留。
用户要求与所选模式冲突时，Interaction 应说明冲突，让用户选择切换模式或取消对应
预算，不自行放宽限制。验证 `identity` 由后端保留，接入工具负责其配置。

### 使用现有实验脚本启动研究

已登记实验命令且没有自动达标目标（`verification.target=null`）时，可以记录比较指标，
由研究员读取脚本输出的成绩，无需额外接入平台 verifier。`verifier_available=false`
仅表示没有平台自动验证器；`experiment_commands_available` 和 `result_evaluation`
说明实验命令及成绩读取方式。自动判定达标仍要求可信 verifier，不能把普通脚本输出
自动提升为平台验证结果。pass@k 仍在启动前检查命令配置是否完整，不执行 smoke。

## 本地验证

`tests/test_preflight.py` 使用本地脚本推理服务，启动实际 Runtime 子进程并执行真实工具桥，
覆盖成功续接、错误记忆、故障重试不重复执行已提交工具，以及重复消息不请求模型。`tests/test_research_intake.py` 的提示词验证
也使用真实后端。业务控制测试的替身直接实现 `AgentRuntime` 接口。

```bash
RSI_TEST_NATIVE_HARNESSES=1 uv run --locked pytest tests/test_preflight.py tests/test_research_runtime_package.py tests/test_research_intake.py
```

默认测试会跳过需要 DSH 安装的用例；上述命令启用 standard 与 Codex 两条实际执行链，
不调用外部付费模型。调度器的消息交付、阶段等待、取消、预算和恢复约定在
`tests/test_research_engine.py` 通过 `AgentRuntime` 接口验证；会话持久化和工具桥在原生
集成测试中验证。当前 ResearchApp 格式支持暂停和继续；旧固定流程的计划、检查点及
旧自建模型会话不再兼容，需要使用新的数据目录创建研究。
