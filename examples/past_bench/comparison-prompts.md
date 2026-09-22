# 本机双窗口启动 Prompt

工作目录：`/Users/jjsong/code/AReaL-RSI`。先选择 Codex 模型 `gpt-6-astra`、推理强度 `xhigh`。
AReaL-RSI 的配置已固定相同研究模型；Task Agent/judge 由共同 lock 固定。
本机模型接口为 `https://x.aixtoken.top/v1`，凭证来自 `.env`，无需 OpenAI 官网密钥。
Codex 自身不自动采用仓库 `.env`；先确认其 provider 使用你指定的代理和对应凭证。
两个实验可以同时启动，官方评测经同一 checkout 的锁串行执行。
这份配置只覆盖三个 memory family，不是完整 PAST-Bench。

## 窗口一：Codex Goal

```text
/goal 在 /Users/jjsong/code/AReaL-RSI 中执行 Codex Goal 组实验：通过优化完整 Hermes Harness，完成 30 个不同候选的有效 PAST-Bench search 评测，最后对冻结的最佳版本做一次 final 确认并交付报告。

首先阅读并遵守 examples/past_bench/comparison.md 和 README.md。当前研究模型必须是 gpt-6-astra、xhigh，不符合就报告配置问题，不替换模型。

固定输入：
- seed：/Users/jjsong/code/AReaL-RSI/.rsi/past-bench/harness-versions/v001/version.json
- lock：/Users/jjsong/code/AReaL-RSI/.rsi/past-bench/operator-aixtoken.lock.json
- 本组产物目录：/Users/jjsong/code/AReaL-RSI/.rsi/past-bench/comparison/codex-goal

只使用 Codex Goal 的研究循环，不启动或调用 AReaL-RSI 优化器，不派生其他研究代理。先校验 seed 版本及协议，复制到本组工作目录；允许修改 prompt.md 和 hermes/ 内全部 Harness 内容，不能修改原始 seed、upstream、适配器、任务、模型或评分规则。

先独立测 seed search baseline。每轮提出假设、实施实质性修改，通过 versions.py save 保存新版本，再用 run.py evaluate --phase search 做完整评测。用 uv run --env-file .env --locked python 调用这些脚本，分别传入固定 --lock、选中版本的 --harness 和新的 --output；不输出密钥或读取私有轨迹，只读取自己的 evaluation.json 聚合结果。保存前后校验版本摘要，不覆盖历史文件。

每轮保存版本、父版本、修改、可信结果、分数、保留决定和资源使用到 rounds.jsonl。保持一个优化进程和一个评测作业。不同候选的有效 search 结果累计达到 30 才进入 final；不提升的有效评测算一轮，空改动、重复候选、失败尝试不算。遵守共同协议的 32 次有效评测、额外最多 6 次失败尝试和 48 小时等预算，不自行增加 diagnostic 或重测次数。

以 search evaluation_lift 选择最佳版本，同时报告开启/关闭持久化的绝对分数，禁止靠削弱关闭组抬高差值。30 轮前不能因满分或停滞宣布完成。冻结最佳版本后，只对它评测一次 final，不用 final 反馈继续优化。不查看另一组的数据或隐藏任务。

仅在 30 轮账本完整、final 确认完成、最佳版本可重新加载且报告落盘时标记 Goal 完成。预算耗尽或不可恢复故障时保存检查点并明确未完成，不能编造轮数或成绩。不要修改仓库公共代码或为实验提交 git；所有工作与报告留在本组产物目录。
```

## 窗口二：AReaL-RSI TUI

本机 `.env` 的 `RSI_RESEARCH_CONFIG` 已选择
`.rsi/past-bench/research-hermes-30rounds-v003.json`。使用
`rsi research --config .rsi/past-bench/research-hermes-30rounds-v003.json` 启动；
以下为旧 TUI 对话记录：

```text
现在启动 AReaL-RSI 组实验：使用已准备的 research-hermes-30rounds-v003.json 配置，优化完整 Hermes Harness 在 PAST-Bench 上的表现，完成 30 个不同候选的有效 search 评测，再对冻结的最佳版本做一次 final 确认。请实际调用研究启动工具，不要只给计划，也不要用交互助手代替 Research Agent 手工优化。

启动前确认配置为 gpt-6-astra / xhigh，seed 为 harness-versions/v001，预算为 38 次总验证、64 个节点和 48 小时；如果仍是 4 次验证的旧配置，请明确报告配置未生效，不启动错误实验。遵守配置内完整的 comparison.md 共同协议。

允许改 prompt.md 及 hermes/ 的执行流程、工具、记忆、上下文、内部提示词和 skills；保留官方接口与 HERMES_HOME 状态契约。不要修改原始 seed、旧版本、benchmark、模型、评分或运行环境。

先测 seed search baseline。每轮提出假设、完成实质修改、保存独立不可变候选，调用可信 search 验证，记录分数与保留理由。每轮应有不同摘要和成功完成的完整评测；无提升算一轮，重复版本和失败尝试不算。不要调用额外 diagnostic，也不要在 30 轮前因满分或停滞结束。

按 search evaluation_lift 选择最佳版本，同时报告持久化开启和关闭的绝对得分，不削弱对照组。30 轮后冻结最佳版本，只让该版本查询 final；不得按 final 再优化，不读取另一组产物或隐藏任务。

逐轮保留假设、父版本、候选标识/摘要、修改、评测标识、分数、保留决定及可获得的时间/token/费用数据。完成后交付 30 轮账本、seed 与最佳 search 对比、最佳 final 绝对成绩、全部版本索引和最佳版本加载方式。若达到预算或遇到无法恢复的故障，保留检查点并报告未完成；不得用已提交作业数冒充有效轮数。
```

两份 prompt 只匹配共同模型、seed、评分协议、信息范围和有效评测次数。
框架只能硬限制总预算；完成状态仍需按不同候选摘要和成功评测结果审计。
Goal 的用法依据 [OpenAI 官方文档](https://learn.chatgpt.com/use-cases/follow-goals)。
