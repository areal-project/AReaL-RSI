# Codex Goal 与 AReaL-RSI：30 轮 Harness 对比协议

目标：在固定 Task Agent 模型 gpt-6-astra 下，优化 Hermes Harness 在 PAST-Bench
上的跨任务学习表现。研究模型也固定 gpt-6-astra、xhigh；judge 由共同 lock 固定。
这是等有效候选评测次数的框架对比，不是严格等 token、等费用或多随机种子的统计实验。

## 共同边界

- 两组从同一 version.json 的完整源码开始，使用同一 operator lock。各自独立运行 seed
  的 search baseline，不复用历史成绩。禁止读取另一组的候选、记录、结果或交流优化经验。
- 只修改工作副本内的 prompt.md 和 hermes/ 全部内容，包括执行循环、工具、记忆、上下文、
  内部提示词和 skills。保留 AIAgent、SessionDB、tools.registry 桥接接口和 HERMES_HOME
  状态契约。不得修改 runner、grader、桥接器、模型连接、任务、预算、划分或依赖环境。
- 不覆盖原始 upstream、seed、已保存版本或已完成评测。每次变更保存为新的独立版本；
  运行时选择其文件路径或不可变 candidate_id，禁止将学习状态藏入源码目录绕过官方状态规则。
- 不读取 benchmark 题目、fixtures、expectations、grader、私有轨迹或其他隐藏评测信息。
  只使用自己的聚合评测结果与允许编辑的 Harness 源码。不得内置答案或预先学得的用户偏好。
- 使用官方适配器完整运行 family 的持久化开关对照。共享 checkout 的文件锁串行调度，
  等锁不算失败；不可直接绕过适配器或清理另一组的进程/容器。

## 次数、选择与完成条件

1. 先评测 seed 的 search baseline（不计入优化轮数）。
2. 完成恰好 30 个有效优化轮次，满足“至少 30 轮”的要求。每轮必须有明确假设、实质性
   Harness 修改、不同于此前有效候选的文件摘要、独立保存版本、一次成功产出可信
   evaluation.json 的完整 search 评测，以及保留/放弃的理由。
   只讨论、只改注释、重复版本、编译检查或失败的评测不算一轮。负分或不提升的有效评测算一轮。
3. 不运行额外 diagnostic/develop 评测或重复测量挑高分。有效预算为 1 次 seed search +
   30 次候选 search + 1 次最终 final，共 32 次。另允许最多 6 次无有效结果的失败尝试，
   总尝试上限 38 次。失败重试必须使用新的输出目录，保留失败证据。
4. 主要选择指标为 search evaluation_lift，同时记录 evaluation_score、
   without_persistence_score、ablation_lift（若存在）。禁止刻意损害关闭持久化组来抬高 lift。
   lift 相同时优先开启组绝对得分更高的候选，再优先改动较少者。seed 也参与最佳版本选择。
5. 满分、连续无提升均不是提前完成条件。完成 30 轮后冻结最佳候选，只查询一次 final；
   不根据 final 结果再优化，不在 final 上测 seed 或其他候选。final 失败只能重试同一版本，
   且仍受总失败次数上限约束。无改善时允许最终选择 seed，必须如实报告。
6. 每组墙钟上限 48 小时、一个优化 Research Agent、最多一个评测作业，不额外派生研究代理。
   框架控制代理不算额外 Research Agent。优化侧保护上限为 4000 次模型调用、5000 万 tokens，
   不包括 Task Agent/judge；若平台不能提供相同计数口径，明确记录“不可直接比较”，不要编造。
   达到任何硬预算、凭证失效或持续基础设施故障时保存检查点并报告未完成，不擅自放宽预算。

## 必须保存的证据

逐轮记录 round、hypothesis、parent_version、candidate_id/文件摘要、修改摘要、评测标识、
三项主要分数、保留决定、耗时、可取得的 token/调用数/费用。失败尝试单独编号，不凑轮数。
每轮都落盘，恢复时从账本继续，不能重置计数。AReaL-RSI 使用框架的候选快照与研究记录；
Codex 使用独立 versions/、evaluations/、rounds.jsonl 和报告文件。

交付：seed search、最佳 search、相对 search baseline 的差值、最佳候选 final 的绝对分数、
有效轮数和失败次数、全部版本/评测索引、可加载的最佳版本标识、复现命令、资源使用和局限。
没有 seed final 时不得声称测得了 final 相对 seed 的提升。只对锁定的 family 子集下结论。

本文件是两组的共同实验约束；候选搜索策略由各模式自行决定。
