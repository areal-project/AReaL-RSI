# v0.2.4：持续观察与可靠消息

新研究默认使用 `passk` + `leader_guidance` + `leader_guidance_v024`。
纯 pass@k、v0.2.1、v0.2.2 和 v0.2.3 的协作流程及角色 Prompt 保留。
v0.2.4 与 v022、v023 模块互斥；已启动研究不会切换版本。

## 协作流程

研究员仍各自完成整题，提交、报告和收到建议都不会暂停研究。
有关键发现、持续困难或反证时调用 `submit_report`，解释结果和适用条件。

RSI 不再提供 `wait`。原生会话结束一个回合后，调度器会在下一次 tick 继续同一会话，
提醒 RSI 查看新进展、核对之前建议的效果、继续思考下一步。
暂停、停止、有效时间耗尽和运行错误的原有处理仍然生效，不用空轮询替代错误重试。

RSI 的 `get_state` 增加各人的近期进展原文、实验入口、当前活动和最近建议的已读状态。
进展文字来自 Runtime 的已完成 assistant 消息；短视图保留最近三条，每条最多 1600 字符，
完整文字保存为当前 run 内可引用的材料。更早上下文仍可用 `read_researcher_trace` 查看。
这些文字不是系统验证的结论，不会自动增加成绩或创建正式报告。
因此，即使研究员忘记报告，RSI 仍能看到他们在做什么。

RSI 根据实际困难定向建议，分享原始证据和适用条件，再观察接收者的结果。
Prompt 强调区分尚未验证的假设，避免重复排查和将局部结果推广到整题；
不要求统一路线，也不把报告频率当作研究质量。

## 通用工程修复

这些修复适用于当前代码中的所有协作模式，和 v0.2.4 的调度增量分开维护：

- Codex 通过原生 stdio MCP 调用 RSI 工具，后面仍是 AReaL Agent Runtime 和原有的
  带权限检查的工具服务。模型、API、密钥、网页工具与 run 隔离规则不变。
- 工具调用持续到服务返回结果，消除原先 shell 一秒后返回 `session_id`、调用者却只读取
  `.output` 的中间环节。`run_job` 返回时，提交输入已经冻结。并行编辑应先保存候选，
  再提交明确的候选 ID；不保证任意并行改写同一文件仍有确定的提交内容。
- 新消息附在任务工具结果中。只有显式调用 `acknowledge_messages` 才确认回合中的新消息；
  未确认就保留并再次提供。确认只表示已读，不表示同意或采纳。
- 确认受当前 agent 的消息范围约束，不能确认其他人的消息。消息、确认和工具操作沿用
  原有持久化记录；重启后未确认消息仍在收件箱。
- 保留给其他原生后端的 CLI 改为仅依赖标准库的小客户端，避免每次启动导入整个 Runtime。

消息仍在下一次 RSI 工具返回或下一回合送达，不打断当前模型推理或正在执行的实验。
持续观察会增加 leader 的模型使用量；是否改善研究成绩需要新实验验证。
复现历史实验的完整执行环境需要原代码版本，选择旧协作模式只固定协作流程与角色 Prompt。

## 文件

- [协作模块](../src/areal_rsi/coordination/continuous_guidance.py)
- [RSI Prompt](../src/areal_rsi/agents/rsi/leader_guidance_v0.2.4.md)
- [Researcher Prompt](../src/areal_rsi/agents/research/leader_guidance_v0.2.4.md)
- [Runtime 消息与权限边界](../src/areal_rsi/modules/research_runtime.py)
- [原生 MCP 传输](../src/areal_rsi/modules/runtime_research_mcp.py)

验证覆盖：无报告时 leader 继续工作、进展不冒充验证结果、暂停/停止、禁止 leader wait、
已读确认与丢弃输出后的再次交付，以及真实 Codex App Server 经 MCP 取得完整工具结果。
这些测试使用本地模型服务，不调用付费模型或 GPU。

已完成全仓回归：357 passed、15 skipped；前端 14 项通过；Ruff 和 Mypy 通过。
另行启用的 standard/DSH 原生集成检查未通过：现有 macOS 隔离拒绝 Runtime 内部
动态 MCP 回环端口。该问题独立于本轮 Codex 路径，未通过放宽 run 隔离来绕过；
standard 的真实链路仍需单独修复和验证。
