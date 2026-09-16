# Agent 后端

当前 ResearchApp 的 `standard` 和 `codex` 都通过 ARealAgentRuntime 接入，可在新建研究和开始前的设置中选择，也可在 ResearchConfig JSON 中设置 `backend`。省略时为 `standard`。API 地址、密钥、模型和推理强度使用同一组研究设置；运行开始后一起冻结。旧固定流程继续通过 `PackageAgentRuntime` 使用同一框架，`RSI_RUNTIME_CONFIG` 和 `examples/runtime/codex.json` 属于该入口。

`standard` 保留自研 ResponsesRuntime 与 DSH 通用工具。`codex` 使用 `openai-codex==0.154.0` 配套的固定版本本地原生 runtime；Python 异步 app-server 适配器管理线程、事件和工具回调。直接使用 JSON-RPC 是为了让业务工具执行期间仍能读取消息和中断回执：该版本 SDK 的同步回调会占用其唯一消息读取线程。

## Runtime 接入

执行路径为 `ResearchApp → ResearchRuntime → ARealAgentRuntime → ConversationHarness → standard / codex`。`ConversationHarness` 实现公开的 `HarnessAdapterPort` 和 `ExecutionChannel`，`ResearchToolProvider` 实现 `ToolProviderPort`；通过公开构造 API 注入框架，复用标准 SQLite StateStore 和 AssetStore，不修改 vendored wheel 或导入框架私有实现。

研究的每个角色保留独立会话，多次阶段执行对应同一 Framework Session 下的多个 Run。工具执行走 `tool.requested → Application 授权与调用 → 持久化 tool.completed → send_tool_result`，后端拿到已提交的回执后才继续或结束阶段。`submit_report` 的 stop 字段随回执一起提交，表示研究阶段结束，并非整个研究被取消。

角色 Prompt 和工具目录由 Runtime 解析后传给执行器。RSI 的收件箱继续保存消息，已有补充消息随 Run 输入进入框架，执行中的新消息使用公开 `steer_run`；后端确认写入原会话后才通知 RSI 已送达。预算策略留在 RSI，Adapter 的同步请求观察接口确保拒绝请求发生在调用模型之前，框架事件保留执行、工具、汇总 usage 与扩展观察记录。

当前 wheel 的内置 Codex Adapter 没有 Application 工具桥，因此这里注册的是 RSI 提供的 Adapter，而非将内置 `codex-cli` 配置改名。现有 Responses 和 Codex 引擎只承担底层模型历史、原生工具及恢复；工作台不直接调用它们。`standard` 也不是对内置 DSH Adapter 的别名，替换其循环会改变既有实验行为。

## 业务与控制边界

- Coordinator 继续创建研究员、分配阶段、保存消息和管理节点。每位 Agent 对应独立持久化 Codex thread；通过 `agents.enabled = false` 关闭原生子 Agent，通过 `features.goals = false` 关闭原生 goal 自动续跑，阶段调度由 RSI 负责。此版本的旧 `features.multi_agent` 开关不能代替 `agents.enabled`，需检查实际模型工具列表。
- 通用能力尽量使用 Codex 原生实现，包括 shell、文件编辑、文件检索和网页调研；`search_web`、`fetch_url`、`list_files`、`read_file`、`write_file` 不作为 Codex 动态工具提供。研究协作、证据、候选管理与实验作业工具仍注册在 `rsi` 命名空间，按角色授权；不需要独立 MCP 服务。动态工具是实验性 app-server 接口，因此固定 SDK/runtime 版本。
- 允许联网时原生网页工具使用 `web_search = "live"`，同时启用 `features.standalone_web_search` 和本地 provider 的 `supports_standalone_web_search`。本地网关把原生 `/alpha/search` 请求原样转发到研究配置的同一上游，使用同一 API key。禁止联网的研究（`metadata.allow_web_access = false`）关闭原生网页工具与该转发通道。上游不支持时保留真实错误，不偷偷改用项目搜索。`standard` 继续使用原有网页工具。
- `send_update` / `share_update` 完成持久化投递后返回，发送方继续工作。接收方正在执行时使用 `turn/steer`，确认 `userMessage` 已写入会话后才从收件箱移除。`turn/steer` 的成功回执只表示进入队列；暂停可能清除此队列，因此未确认的消息会随下次合法唤醒一并补投。等待中的 Agent 仍由现有状态机判断是否应唤醒，正式报告后不会被普通进展消息唤醒。
- `submit_report` / `wait` 等停止工具先保存业务结果与回执，再中断当前 Codex turn；本地模型网关同时停止接受后续请求。下一次派单恢复原 thread。
- 原生命令使用 workspace-write 沙箱与网络访问，长实验仍经登记的作业工具管理。模型上游密钥不会传给 Codex 或其 shell；每次连接使用临时本地网关凭据。

## 记录与恢复

每个 Agent 会话新增 `areal-runtime/<配置摘要>/`：`state/state.sqlite3` 保存框架权威事件与状态，`assets/` 保存框架素材，`active.json` 关联 RSI 消息、Framework Run 和 Session。已完成的同一消息直接读取已提交结果，不重复创建 Run 或累计 usage；暂停结束当前 Framework Run，下一次合法唤醒由原后端继续未完成的任务。异常退出的未完成 Run 由框架恢复，Adapter 只接受当前 RSI 消息身份。研究开始前显式修改模型、Prompt 或工具配置会生成新的不可变框架绑定，后端原始历史继续保留。

会话目录包含 `configuration.json`、`codex-session.json`、`codex-events.jsonl`、Codex 自己的 `codex-home/`，以及逐请求 `requests/`、`responses/`。业务工具回执记录调用 ID；恢复时用同一 ID 对照现有业务回执，不盲目重复已接受的实验。普通原生 shell 的副作用仍由 Codex 根据线程历史和工作区状态恢复。

原生网页请求和原始回执另外保存在 `web/requests/`、`web/responses/`。检索命令、实际返回文本、结果链接和 HTTP 状态自动归档到本研究的 `materials`，可通过研究记录工具引用、共享；私有会话输入和加密续接数据不复制到公开资料。归档不重写发给 Codex 的原生响应。网页调用单独记为 `web_search_request` / `web_search_response`，不冒充一次模型推理；后续模型读取结果产生的 token 仍按实际推理请求计入预算。

2026-09-15 早期八题 Codex 实验使用旧的自定义网页工具；原生搜索改动发生在六题结束后，既有结果不能当作原生搜索实验。新研究采用上述配置；旧线程保存的历史工具与轨迹保留，不通过改写历史记录迁移工具表。

模型请求由本地流式网关转发到研究配置的地址。转发前执行原有预算检查，完成时先归档 usage 再结算；一次 Codex turn 可能产生多次请求，不能作为一次 API 调用计数。Codex 重发到网关的请求也各自计数；standard 的重试仍沿用其原有逻辑请求与 attempt 记录。无可靠 usage 的中断请求保留预算预留。 连接失败与流式传输中断保留异常类型并记录在 responses/*.error.json，以可重试的传输错误恢复原会话；空异常信息不会被误判为不可重试的参数错误，重试不重置预算。

Codex 管理自己的上下文压缩；`context_window_tokens` 和 `max_output_tokens` 沿用研究配置，standard 的摘要提示和近期上下文保留策略不控制 Codex。两种框架在工具呈现、系统提示和压缩机制上存在差异，成绩比较需要同时查看请求量、token、实验与协作记录。

RSI 的完整角色提示从 leader 职责开始，依次包含调研与团队计划、协作、证据判断、当前后端工具、共享沟通和研究诚信。Engine 按任务配置填入 `max_agents`，将工具说明放在对应章节，避免重复追加；共享诚信声明位于角色提示末尾。任务目标、验证约束和实时状态通过运行时快照提供。

Research 默认使用简略版 Prompt，创建研究时可在 ResearchConfig 中设置 `research_prompt_variant: "detailed"` 使用详细版；省略或设为 `"simple"` 使用简略版。两版均按实际后端插入工具说明，不重复追加沟通和诚信规则，阶段内消息与正式报告后的等待机制一致。版本说明与对照文本见 [Research Prompt](prompts/README.md)。Interaction 使用自然语言整理需求、转交研究期间的补充，其启动和授权规则保持不变。

- `standard`：在回合开始及工具序列完成后的边界检查上下文；默认达到 `(context_window_tokens - max_output_tokens) × 85%` 时压缩，并用实际 usage 校准估算。独立的摘要请求概括较早历史，通常保留最近约 8,000 token；当前指令和系统状态另行原样保存。后续执行请求始终重新发送完整角色及工具提示，并附恢复说明，不依赖摘要记住这些规则。显式 `compact_after_chars` 可覆盖这一策略，设为 0 则关闭。
- `codex`：每次 `thread/start` 或 `thread/resume` 都传入完整 `developerInstructions`，每次 `turn/start` 都附当前运行时快照。压缩由原生 runtime 触发；项目没有设置 `model_auto_compact_token_limit`，使用原生模型默认值。2026-09-15 最近三题的真实 RSI 归档确认：压缩后的请求仍原样携带完整 developer 提示，但位置可在保留消息和摘要之后，并非固定的第一条消息。
- 运行时快照包含完整任务说明、计划、起始产物、最佳与 baseline 结果、撤回记录、研究员状态、当前阶段与任务、预算和待用户处理事项。Codex 同一回合内压缩时，适配器没有额外注入最新快照的钩子，依赖原生历史保留与摘要；需要最新事实时用 `get_state`，下一次回合开始会重新注入快照。完整角色规则仍在，不等于全部实验细节无损保留。

开始研究前切换后端会保留旧会话归档，并把已记录的用户对话带入新后端会话；已启动研究只能用原后端恢复。`codex-session.json` 与 `session.json` 不互相转换。

接口依据：[Codex SDK](https://learn.chatgpt.com/docs/codex-sdk)、[Codex app-server](https://learn.chatgpt.com/docs/app-server)、[原生网页搜索](https://learn.chatgpt.com/docs/web-search)。
