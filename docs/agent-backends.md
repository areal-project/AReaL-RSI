# Agent backends

ResearchConfig 的 `backend` 保留两个选项：`standard`（默认）和 `codex`。
两者均通过仓库原有的 `PackageAgentRuntime` 使用 AReaL Agent Runtime 的公开
`build_composition` / Application API。RSI 不再维护独立的 Codex App Server 客户端、
请求代理或研究模型执行循环。`ResponsesRuntime` 仅保留旧协议的回归测试与历史兼容代码，
不再是工作台的后端。

| 选项 | Runtime 适配器 | 文件与命令 | 联网 |
| --- | --- | --- | --- |
| standard | dsh | Runtime 的 workspace tools | RSI search_web / fetch_url |
| codex | codex-cli | Codex 原生工具 | Codex 原生 web search |

两个后端使用同一份研究配置中的模型、API 地址和密钥。DSH 通过原生 provider 配置使用
Responses 协议，并明确声明模型的推理强度。Codex 使用锁定版本的 CLI，由 Runtime 管理
App Server；原生搜索保留，原生 subagent 和 goal 禁用。无需 OpenAI Codex Python SDK。

## RSI 任务工具

DSH 通过 Runtime 的 ToolProvider 插件调用 RSI 工具。当前版本的 Runtime Codex 适配器
不能接入 Application ToolSpec，因此 Codex 使用原生命令工具调用沙箱内的 RSI 工具客户端。
客户端提供工具列表和参数说明，通过每个会话独有的能力令牌访问父进程。
父进程校验工具、参数及当前 agent 权限，沿用已有的动作幂等记录、作业和验证流程。

已有协作模式仍使用持久化收件箱。执行中，在 RSI 任务工具返回时附带待收消息；正式报告
后继续等待。当前包的 Codex 适配器没有运行中 steer 能力，因此不能承诺在任意原生工具
边界立即注入消息。隔离 pass@k 不依赖这项能力，也不开放跨 agent 消息。

## 会话、停止和记录

每个会话有独立工作区、原生运行目录和 macOS `sandbox-exec` 子进程。原有的隐藏评测数据
和其他研究员工作区不向子进程开放。RSI 管理任务和正式验证，Runtime 管理推理、工具、
上下文压缩及供应商重试；不会把 Runtime 当成外壳再运行自定义 harness。

完整角色 prompt 绑定在 Runtime 的 AgentEnvironment 中。每个 RSI 回合提供当前任务状态，
后续回合使用原生会话续接；压缩由各自 harness 负责。进程重启时重放已提交的 Runtime
journal，恢复环境和原生 session binding；不把旧 Responses/Codex 自建会话格式当作新会话。
新的模型配置会在研究开始前创建新会话并保留已记录的用户对话。
`context_window_tokens` 沿用原有默认值并传给两个后端；旧压缩摘要和
`compact_keep_recent_tokens` 不控制原生压缩。

Runtime journal 是执行事实来源，包含原生事件、工具执行和 usage。工作台汇总供应商实际
报告的 token，并单独记录 runtime turns。当前 Runtime 的两个适配器没有逐次 model-call
数据；runtime turn 不等于一次模型请求。原先请求代理的逐请求预算预留和请求文件不再生成，
不能用该字段进行请求数或精确费用比较。新实验应关闭 model-call 次数限制；token 限制按
已收到的 usage 停止，不代表供应商账单的严格上限。中断请求可能没有完整 usage。

停止会取消原生执行和受管作业；探索时限使用持久化的启动时间，不因恢复会话而重置。
正式复测沿用原有 finalizer。具体实验约定见 [pass@k](passk.md)。
