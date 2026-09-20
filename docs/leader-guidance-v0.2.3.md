# v0.2.3：精简的 RSI 协调提示

v0.2.3 同时精简 leader 和 researcher 的角色 Prompt，保留独立完成整题、
定向共享、自主判断和持续探索。反复受阻时重新审视假设，用实验区分不同解释；
具体疑点按任务要求验证，不增加针对某道题或某种实现的规则。

角色正文分别位于 [RSI Prompt](../src/areal_rsi/agents/rsi/leader_guidance_v0.2.3.md)
和 [Researcher Prompt](../src/areal_rsi/agents/research/leader_guidance_v0.2.3.md)。
公共工具说明、诚信要求与跨 run 边界保持不变。

新增模块 `leader_guidance_v023` v1 依赖 `leader_guidance` v3，只替换两份 Prompt；
工具、调度、消息、计时和评测均复用现有实现，不与 `leader_guidance_v022` 同时选择。

v0.2.3 保留为可选版本；当前默认版本见 [v0.2.4](leader-guidance-v0.2.4.md)。本版配置为：

| 选择 | `mode` | `collaboration_modules` |
| --- | --- | --- |
| 纯 pass@k | `passk` | `[]` |
| v0.2.1 | `passk` | `["leader_guidance"]` |
| v0.2.2 | `passk` | `["leader_guidance", "leader_guidance_v022"]` |
| v0.2.3 | `passk` | `["leader_guidance", "leader_guidance_v023"]` |

历史版本的 Prompt 与指纹不变，启动后的研究保持冻结配置。
对照实验应在同一代码、模型、任务和预算下切换上述选项；选择旧 Prompt 版本不等于恢复旧工程实现。
