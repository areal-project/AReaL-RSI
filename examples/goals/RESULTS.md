# 实现与实测验收 · 2026-10-16

Goal 生命周期、CLI/TUI 共享服务、双 Target 执行、独立验收、版本化 Skill、只读看板及 Model 模拟契约已交付。功能验证通过；真实效果按冻结门槛分别报告，未达标项保留原始结果。

| 实验 | 冻结协议结果 | 结论 |
| --- | --- | --- |
| Harness | 基线 54/60 → 候选 58/60；格式均 100%；增益 6.67 个百分点 | 未达到 +10 个百分点门槛，效果验收失败 |
| Artifact A：任务管理 | 桌面、移动端全部十项必过检查通过 | 独立验收通过 |
| Artifact B：三对书签运行 | 六次运行均通过十二项检查，含 URL 保留及非法 URL 拒绝 | 质量通过；整体效率收益未证明 |

Harness 的最终候选在独立验收前已冻结，最终失败后没有继续搜索，也没有调整门槛。正式运行使用冻结的旧实现；开发增益停止条件随后修复并通过工作流契约测试，旧结论没有重算或覆盖。

三对运行按 AB / BA / AB 次序，隔离工作区、Memory、缓存和轨迹，比较模型内容摘要、Runtime、初始产物、完整契约、任务、验收器和实现身份。三组只在 Skill 选择上不同。模型均为 gpt-6.1-sol，实际请求 max_tokens=12288、流式输出，未设置温度或种子；后端默认采样条件不可精确复现。

| 配对 | 无 Skill 达标秒数 | Skill 达标秒数 | 质量 |
| --- | ---: | ---: | --- |
| 1 | 686.6 | 476.9 | 两组通过 |
| 2 | 338.5 | 429.5 | 两组通过 |
| 3 | 824.5 | 1256.0 | 两组通过 |

预先声明的中位数从 686.6 秒降至 476.9 秒，减少 **30.5%**，满足该项 20% 时间门槛。不过三对中有两对更慢，Skill 组总生产时间为 2162.4 秒，对照为 1849.6 秒，**增加 16.9%**。第三对无 Skill 运行发生一次重试；Skill 运行的首轮页面存在 JavaScript 错误，第二轮修复后才达标。三对 demo 不构成普遍效果或统计显著性的证明。

Skill 来源于 A 的开发改动和 TRAIN/VALIDATE 聚合证据，没有读取隐藏 FINAL 明细。首次提炼在 600 秒预算后超时，模型已写出的文件经过单独恢复打包，原失败保留。随后完成九项通用场景自测，并通过单独 CLI 操作显式激活具体版本；操作者为 Codex，授权来自用户要求执行完整 demo 的委托，记录中保留了该身份。

正式 B 轨迹均有原生 Skill 加载调用，但没有附属资源读取或脚本执行。独立的后续能力检查确认了同一 A 来源 Skill 的真实加载、清单读取和脚本成功执行，用时 49.7 秒。该检查不计入配对时间，不作为生产效率证据。目录枚举与资源读取、执行分开记录，语义影响保持未知。

提炼失败、恢复打包和独立资源核验的已知一次性耗时至少 **654.4 秒**，还未计入未记录时长的验证开销。这三对的时间成本未摊平。Token 计数不完整且字段不一致，失败未提交会话可能漏计；费用、人工时间和完整端到端成本未知，不能宣称综合效率达标。

完整测试为 **201 passed、11 skipped、52 subtests passed**。Model 的提交、状态、取消、恢复及父版本/checkpoint 关系仅以模拟后端验证，真实训练明确拒绝执行。原生 DSH TUI 以真实模型完成三轮提问和修正后的 ready 草稿，未确认、未启动。浏览器核对了面板状态、分数、最终时间轴、重试历史、Skill 资源计数、截图和隔离预览；失败版本的预览错误单独保留。

逐项结果与复跑入口：

- [14 项验收审计](acceptance-audit.json)、[完整检查记录](results/implementation-verification.json)、[成本记录](results/cost-evidence.json)
- [Harness 最终报告](results/harness-final.json)、[交付候选](deliveries/ledger-harness/harness/prompt.md)、[冻结实现](deliveries/frozen-demo-implementation.tar.gz)
- [任务管理报告](results/artifact-final.json)、[交付网页](deliveries/task-app/artifact/index.html)
- [三对全部结果](results/skill-reuse.json)、[中位数、总耗时及失败分析](results/skill-reuse-analysis.json)、[六组交付物](deliveries/bookmarks/)
- [Skill 包](deliveries/research-skill/package/SKILL.md)、[来源与生命周期](deliveries/research-skill/record.json)、[独立资源能力核验](results/a-derived-skill-resource-capability.json)
- [原生 TUI 证据](results/native-tui-clarification.json)、[复跑指南](README.md)、[Goal 配置与接口](../../docs/goal-mode.md)

冻结运行结束后补齐了失败尝试持久化、严格资源计数和最终验收时间坐标。旧运行缺失的首次重试原因标为未知；历史记录不被改写。看板依据真实记录投影，辅助诊断与效果评测分别报告。

浏览器依赖恢复检查使用固定交付物与新场景种子，先得到 blocked（CLI 退出码 5），恢复浏览器路径后沿用同一 revision 达标（退出码 0），没有模型请求。结束执行时释放 Run 锁的修复有稳定回归用例；修复后的全量检查为 201 项测试通过，Ruff、98 个文件的 Mypy、JavaScript 语法及离线锁文件检查均通过。
