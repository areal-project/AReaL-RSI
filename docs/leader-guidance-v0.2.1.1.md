# v0.2.1.1：区分问题与成果分享

基于 v0.2.1；启动配置为 `mode=passk`、`collaboration_modules=["leader_guidance", "problem_sharing"]`。
旧版本和默认选项保持不变。固定人数、独立探索、有效时间、非阻塞消息和最终复测均沿用原流程。

完整角色 Prompt：

- [RSI Agent](../src/areal_rsi/agents/rsi/leader_guidance_v0.2.1.1.md)
- [Research Agent](../src/areal_rsi/agents/research/leader_guidance_v0.2.1.1.md)

Markdown 中的 `max_agents` 和 `tool_instructions` 仍由原有统一流程填充。

## 传播规则

`advise_researcher` 增加 `experience` 参数：`id`、`kind`（`problem` / `positive`）、
`source_agent_id`。正文写经验与适用条件，原始证据继续使用 `references`。
围绕接收者自身工作的普通建议使用 `experience=null`；携带同伴证据时不能省略登记。

同一个 problem 在整个研究期间，原发现者以外最多增加一名接收者。重复工具调用、后续回合和
恢复进程都不会重置额度；向原发现者或已有接收者补充信息仍可进行。
解决后沿用同一 id，以 positive 和新的验证记录发送，允许分享给多人；解决者可以是另一位研究员。
其他已验证正向成果也可从一开始登记为 positive。leader 自己发现的问题以 rsi 为来源，同样只给一人。

系统验证当前 run 的引用、来源和接收人数，并要求问题转正时有新记录。
**经验分类、不同文字是否在说同一问题，以及新记录是否真的证明解决，仍由 leader 判断。**
没有增加语义审核模型；Prompt 明确禁止改名、分批、借普通建议或正向成果夹带同一未解决问题。

传播记录存入 `coordination/state.json` 的 `problem_sharing`，工具动作保存完整参数与消息引用。
RSI 的状态及会话恢复包含该记录。研究员的 `get_state` 不再自动展开同伴报告，避免旁路广播；
仍允许主动读取本 run 的原始轨迹与证据。这是 leader 传播限制，不是重新建立同伴资料的强隔离。
跨 run 隔离不变。

researcher 将 leader 视为掌握全局信息的同事，依据证据决定采纳、调整或拒绝建议。
已确认违反任务要求的错误仍需报告、修复和撤回，不以传播限制隐瞒已知问题。
