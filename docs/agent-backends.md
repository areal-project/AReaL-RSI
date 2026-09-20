# Agent 后端与 Runtime

ResearchConfig 的 `backend` 支持 `standard`（默认）、`codex` 和 `runtime`。
所有后端均通过 `PackageAgentRuntime` 使用 AReaL Agent Runtime 的公开
`build_composition` / Application API。RSI 不再维护独立的 Codex App Server 客户端、
请求代理或研究模型执行循环。旧 `ResponsesRuntime` 及其自建请求、重试、压缩循环
已删除。连接配置位于 `infra/config.py`，研究回合接口位于
`api/interfaces.py`，桥接实现在 `infra/runtime/research_runtime.py`；
`rsi preflight` 直接验证工作台实际使用的包运行时。

| 选项 | Runtime 适配器 | 文件与命令 | 联网 |
| --- | --- | --- | --- |
| standard | dsh | Runtime 的 workspace tools | RSI search_web / fetch_url |
| codex | codex-cli | Codex 原生工具 | Codex 原生 web search |
| runtime | 由配置文件选择 | 由所选适配器提供 | 按适配器及 allow_web_access 配置 |

同一研究的角色共用固定的模型连接，Task 评测使用独立的 `RSI_TARGET_*` 或远端模型引用。
DSH 与 Codex 使用研究配置中的模型、API 地址和密钥。DSH 通过原生 provider 配置使用
Responses 协议，并明确声明模型的推理强度。Codex 使用锁定版本的 CLI，由 Runtime 管理
App Server；原生搜索保留，原生 subagent 和 goal 禁用。无需 OpenAI Codex Python SDK。

每个会话在独立 Python worker 中调用公开 composition/Application API，无需部署
Runtime HTTP 服务。研究模型通过 `RSI_MODEL / RSI_BASE_URL / RSI_API_KEY / RSI_REASONING_EFFORT`
配置。Runtime wheel 为 `0.4.0.dev1+rsi1`，来源、摘要和重建方式见
[vendor](../vendor/PROVENANCE.json)；已有 Codex 可用性事件补丁服务于[有效时间](effective-time.md)。
DSH 首次启动安装随包冻结的 CLI/profile 依赖。

## 其他 Runtime 后端

`examples/runtime/` 保留 Claude Code、Claude Agent SDK、Anthropic、Hermes、
remote、entrypoint 和混合角色示例。这些是适配器配置，不是另一套编排器；仍由 ResearchSystem
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
Hermes 已验证版本为 0.17.0（commit `2f5950a83a66d2b91918bb78e6d1b60f3f48b938`），
Tirith 为 0.4.1。需要安装源码、Python 环境及真实 Tirith 扫描器，更新示例中的路径；
通过原生 terminal 调用 `rsi-tools`。remote/entrypoint 需要管理员部署相应服务或插件。
配置仅供受信操作员设置，不能内嵌 API key；使用研究凭证或 `authTokenEnv`。

## RSI 任务工具

DSH 通过 Runtime 的 ToolProvider 插件调用 RSI 工具；Codex/Hermes 通过原生命令工具
调用沙箱内的 RSI 工具客户端。
客户端提供工具列表和参数说明，通过每个会话独有的能力令牌访问父进程。
父进程校验工具、参数及当前 agent 权限，沿用已有的动作幂等记录、作业和验证流程。
工具调用的去重身份由会话目录绝对路径、RSI 逻辑消息 ID 和原生调用 ID 确定。
不同消息可使用相同的原生调用 ID；同一消息在进程重启或显式重试后仍使用原身份，
参数不一致的重试仍会被拒绝。该规则统一生效，不兼容旧会话中未分域的调用记录。
不能直接以原生 Run ID 分域，因为它会在恢复时改变。

已有协作模式仍使用持久化收件箱。执行中，在 RSI 任务工具返回时附带待收消息；正式报告
后按[pass@k 生命周期](passk.md)继续探索或等待作业。当前 Codex 适配器没有运行中 steer
能力，不能承诺在任意原生工具边界立即注入消息。隔离 pass@k 不依赖这项能力，也不开放跨 agent 消息。

## 隔离边界

默认 `sandbox` 模式下，每个会话有独立工作区、原生运行目录和 macOS
`sandbox-exec` 子进程，隐藏评测数据和其他研究员工作区不向子进程开放。

worker 只复制执行所需的模块，禁止读取完整 RSI 包中的评测答案；
写权限限定在自己的工作区和会话目录。所选 Python 解释器的运行库可以只读访问。
`readable_paths`、模型端点和插件由受信操作员配置，候选不能自行扩大权限。

DSH/Claude 的上游 MCP 工具桥动态分配端口，因此允许访问 `localhost:*`，
不提供精确的本地服务隔离；文件沙箱及 RSI 工具桥的令牌、角色检查仍生效。
这不需要给 Runtime 增加端口补丁。其他后端按配置开放工具桥和模型服务端口。

Codex 在 macOS 沙箱中使用进程专属的 HTTPS CONNECT 通道，避免依赖不可访问的系统网络服务。
通道只转发加密字节，不改写模型请求或重试策略；默认仅允许公网 443，另外允许明确配置的
模型端点。解析后连接固定 IP，拒绝其他本地/私网目标；各进程仅开放自己的通道端口。
CA 证书使用 `SSL_CERT_FILE`，未配置时使用 certifi；实现见
[runtime_network.py](../src/areal_rsi/infra/runtime/runtime_network.py)。

可信 Linux 服务器可在启动工作台前显式设置：

```dotenv
RSI_RUNTIME_ISOLATION=linux-unrestricted
```

它直接启动独立 Python 子进程，使用 bash，
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

## 会话、停止和记录

`runtime_storage` 将事件和状态提交到 `data/state/journal.jsonl` 后再向消费者发布。
`session-binding.json` 固定原生会话身份，`run-bindings.json` 记录原生 Run；轨迹导出是日志投影。
缺依赖、工具或配置错误不会被记为成功；取消会请求原生运行停止并清理子进程组。

完整角色 prompt 绑定在 Runtime 的 AgentEnvironment 中。每个 RSI 回合提供当前任务状态，
后续回合使用原生会话续接；压缩由各自 harness 负责。进程重启时重放已提交的 Runtime
journal，恢复环境和原生 session binding；不把旧 Responses/Codex 自建会话格式当作新会话。
新的模型配置会在研究开始前创建新会话并保留已记录的用户对话。
`context_window_tokens` 可传给 DSH、Codex 和 Hermes。省略时 Codex 使用原生默认窗口；
DSH/Hermes 使用 RSI 默认值：`gpt-6-astra` 为 256,000 token，其他模型为 128,000。
DSH 支持 `max_output_tokens`；Codex 沿用原生输出限制，传入非默认值会被拒绝。其他
适配器的限制取决于原生能力，不能假定所有后端都会执行同样的参数。Codex CLI 固定为 `0.153.4`。

新 `ResearchConfig` 不再包含 `compact_keep_recent_tokens`、`max_model_calls_per_turn`、
`retry_initial_seconds` 和 `retry_max_seconds`。配置只接受当前字段，旧字段会明确报错，
不自动迁移历史配置。这些设置之前未传入原生后端，不能继续把自定义值当作已经生效。
重试和压缩由原生 harness 管理，研究的时限和 token 停止条件使用 `brief.limits`。

Runtime journal 是执行事实来源，包含原生事件、工具执行和 usage。工作台汇总供应商实际
报告的 token，并单独记录 runtime turns。RSI 没有可跨后端统一执行预算的逐次 model-call
计数；runtime turn 不等于一次模型请求。原先请求代理的逐请求预算预留和请求文件不再生成，
不能用该字段进行请求数或精确费用比较。`ResearchLimits.max_model_calls` 保留为可空兼容字段，默认 `null`；
当前原生运行时无法执行该上限，非空值会明确报错，需求工具和工作台不提供这个设置。
token 限制按已收到的 usage 停止，不代表供应商账单的严格上限。中断请求可能没有完整 usage。
工具参数定义改变后不自动迁移旧草稿会话，需要新建研究。

停止会取消原生执行和受管作业；探索时限使用持久化的启动时间，不因恢复会话而重置。
正式复测沿用原有 finalizer。具体实验约定见 [pass@k](passk.md)。

## 验证

真实模型预检的命令与输出位置见 [README](../README.md#检查研究运行时)。预检检查工具调用、
进程重启后的原生会话记忆和重复消息回执，不提交正式评测或 GPU 作业。

```bash
RSI_TEST_NATIVE_HARNESSES=1 uv run --locked pytest \
  tests/test_preflight.py tests/test_research_runtime_package.py tests/test_tool_call_identity.py
```

| 测试 | 范围 |
| --- | --- |
| `test_research_runtime_package.py` | 原生后端工具、隔离、会话、取消与 usage |
| `test_preflight.py` | 真实 Runtime 子进程、本地脚本模型、工具、记忆与重复消息 |
| `test_tool_call_identity.py` | 不同消息的工具 ID、同消息重试和持久化副作用 |
| `test_runtime_package_execution.py` | 适配器工具、MCP、事件及取消契约 |
| `test_research_engine.py` | 调度、消息交付、等待、取消与预算 |
| `test_benchmark_research.py` | 候选及评分身份、TRAIN/VALIDATE 输出隔离 |

默认跳过未安装的可选原生后端；设置上述变量启用 DSH 用例，Hermes 另需
`RSI_TEST_HERMES_ROOT` 指向实际安装目录。这些测试使用本地脚本模型，不代表模型效果。
端到端验收还需真实 ResearchSystem 记录、候选、实验作业及独立最终评分。
