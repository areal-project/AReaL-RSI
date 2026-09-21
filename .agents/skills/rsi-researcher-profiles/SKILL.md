---
name: rsi-researcher-profiles
description: 为 AReaL-RSI 的新 benchmark 生成可复用的 researcher 人设资产，并在已有研究配置中选择它们，用于 v0.2.1.2 实验。
---

# 为新 benchmark 准备 researcher 人设

先阅读仓库中的 `examples/researcher_profiles/README.md`，了解资产格式和启动入口。
目标是让几名研究员以不同偏好独立完成同一个任务，不是把任务拆成几个专家分工。

## 设计人设

读取用户给出的 benchmark 说明、任务样例、允许使用的工具、评测方法和已有研究配置。
理解它主要有哪些不同的解题途径、瓶颈与验证成本；必要时调研允许访问的领域资料。
不读取其他实验答案、隐藏测试或参考解，不把某道题的解法写进通用人设。

默认保留内置的 `general` 综合研究员，再设计三种适合这个 benchmark 的研究偏好。
人数或身份由用户指定时按其要求。偏好要对应不同的观察角度、资料来源或初始尝试，
不要只换职业名称，也不要把 KernelBench 的 GPU 身份套到无关任务上。

每份 prompt 用几段自然语言说明：习惯先看什么，优先查哪些资料或尝试哪些方法，
如何用完整任务的实验判断收益。根据任务复杂程度决定长度，不堆细则。
明确它仍独立完成调研、实现、验证和改进，身份只是偏好，可以改变方法。
不要重复 leader 的协作规则、工具手册、预算和作弊禁令；这些已有公共 prompt。

## 输出资产和配置

在仓库根目录的 `assets/researcher_profiles/<bench>/` 下写 Markdown 文件，例如 `structure.md`。
每个文件使用 YAML frontmatter 的 `name`、`reminder`，正文是身份 prompt。
`reminder` 用一句话概括偏好；资产 ID 是相对于资产目录的路径，不含 `.md`。
不同实验条件需要保留不同人设时，使用不同 ID，不覆盖已有版本。

对用户提供的可运行 ResearchConfig JSON 做最小修改：

- `mode` 使用 `passk`；`collaboration_modules` 使用
  `leader_guidance`、`problem_sharing`、`researcher_profiles`，即 v0.2.1.2。
- `researcher_profile_ids` 按启动顺序列出 `general` 和生成的人设 ID。
- `researcher_profile_dir` 指向资产目录，相对于配置文件；`brief.limits.max_agents` 与 ID 数量一致。
- 保留该 bench 的题目、评测命令、环境、模型连接和预算。人数变化时核对原有节点/作业额度。

没有可运行的 bench 配置时，只生成资产和配置片段，说明还缺哪些启动信息；不要伪造评测器。

用项目 Python 执行 `python -m areal_rsi.researcher_profiles list --directory assets/researcher_profiles`
确认人设可读取；有配置时用 `load_config(...).freeze_researcher_profiles()` 验证 ID 和人数。
这两个检查不调用模型或执行实验。最后给出各身份的一句话说明、文件位置和启动命令。
只有用户要求运行实验时才启动研究。
