# 双 Target 与跨任务复用 Demo

完整实测结论见 [验收结果](RESULTS.md)：Artifact A 与六次书签生产通过，Harness 增益门槛未过；Skill 中位数时间门槛通过，但总耗时回归，不宣称整体效率提升。

交互复跑 Harness 请使用 [TUI 操作指南](../../docs/harness-tui.md)，其中给出了新实验目录、未使用的题目种子、可粘贴的目标与看板入口。

真实运行需要配置 Teacher/Target 模型、DSH、Node/pnpm 及 Chromium，使用仓库根目录启动：

```bash
uv sync --locked --extra browser
uv run --extra browser playwright install chromium
uv run --extra browser python examples/goals/run.py harness --root .rsi/demos/harness
uv run --extra browser python examples/goals/run.py artifact --root .rsi/demos/artifact-a
RSI_DATA_ROOT=.rsi/demos/artifact-a uv run rsi skill list
RSI_DATA_ROOT=.rsi/demos/artifact-a uv run rsi skill validate SKILL_ID --version 1.0.0
RSI_DATA_ROOT=.rsi/demos/artifact-a uv run rsi skill activate SKILL_ID --version 1.0.0
uv run --extra browser python examples/goals/run.py reuse --root .rsi/demos/reuse --skill ~/.areal_rsi/skills/SKILL_ID/1.0.0/package
RSI_DATA_ROOT=.rsi/demos/artifact-a uv run rsi web
uv run --extra browser python examples/goals/report.py --root .rsi/demos/artifact-a
```

protocol.json 预先声明门槛及三对运行次序；每次运行保存协议摘要、提交契约、CLI NDJSON、诊断及结果。重复 runner 读取已有 demo-result，不自动重新评测。要重新进行独立实验请用新的数据目录，并按协议避免重用已暴露的最终任务。

Harness 使用固定种子生成的 TRAIN 30、VALIDATE 30、FINAL 60 道无重复账本算术题，包含整数金额、逐步四舍五入、退款、服务费及结算费。最小可用 Harness 保留原有工具、Skill 和 MCP，只将输出格式交由任务的 JSON 契约指定。简单整数/有理数预检饱和后，账本开发预检 6 题答对 5 题，格式全部合规；该小样本仅用于选择任务，不构成效果验收。正式实验使用新种子，冻结两小时预算，原因及原始门槛见 protocol.json。最终正确率 ≥90%、较冻结基线增益 ≥10 个百分点、输出合规率 100%；不能事后改门槛。

report.py 只读取持久状态和检查点，展示当前阶段、完成轮数、逐任务证据及独立验收；不会发起模型请求。旧的 CLI receipt 是当时的调用结果，恢复后的状态以 Goal 记录及新报告为准。流式代理将连续文本或工具参数合并为较大的分片，完整上游原文仍保存供审计；不改变模型返回内容。

Artifact A 是任务管理；后续 B 是书签管理，各自从独立的未完成 starter 开始。浏览器功能契约在 task.json/bookmark.json 的 description 冻结：

- 静态 index.html 和本地资源，不依赖远端服务。
- data-testid：title-input、add；行 item 中含 edit、edit-input、save、toggle（checkbox）、delete。
- 筛选器 filter-all、filter-active、filter-completed。B 另有 url-input，toggle 对应收藏星标；书签显示可用 HTTP/HTTPS 链接，编辑与重载保留 URL，拒绝空白、非法、相对及 javascript URL。
- 新增、编辑、切换、筛选、重载持久化、空白拒绝、长 Unicode/HTML 字面文本、安全渲染、删除。
- 桌面 1280×900、移动 375×812：无横向溢出，主控件及行至少 24×24，无阻塞交互的 pageerror。

截图仅作为外观与版本证据；功能结论来自确定性的真实浏览器操作。FINAL 使用独立输入与浏览器上下文，结果不传给当前研究会话。

任务管理网页已完成一次真实模型验收：revision 2 的第 1 轮候选通过全部桌面与移动端最终检查。[导出源码](deliveries/task-app/artifact/index.html)、[验收报告](results/artifact-final.json)和[移动端截图](results/artifact-final-375.png)可直接检查。该成功 revision 用时约 372 秒；更早的失败、调试和恢复成本单独保留，不能将该时长解释为完整交付成本。总体进度及尚未通过的项目见 [验证记录](results/implementation-verification.json)。

B 三对对照隔离 Memory、工作区、缓存与轨迹，唯一配置差异是冻结的 Skill 版本。报告全部运行及失败比例、时间中位数、提炼成本与不可观测费用。未过效率门槛只证明复用功能，不宣称效率提升。

Harness 正式最终验收完成：固定基线 54/60，冻结候选 58/60，格式合规均为 100%。提升 6.67 个百分点，未达到 10 个百分点门槛，结论为效果验收失败。[报告](results/harness-final.json)、[候选 Harness](deliveries/ledger-harness/harness/prompt.md)与[冻结实现归档](deliveries/frozen-demo-implementation.tar.gz)保留供检查。本次搜索已关闭，最终反馈不再用于当前选版。后续修复不会改写该正式结论。

原生 DSH TUI 的三轮提问、模型 MCP 调用及未确认草稿见[真实终端报告](results/native-tui-clarification.json)。首次字段填充错误被拒绝，修正后保存 ready revision；没有启动后台任务。

A 的开发轨迹提炼出 [evidence-guided-interactive-state-skill 1.0.0](deliveries/research-skill/package/SKILL.md)，含通用检查清单与 Python 场景脚本。首次模型提炼在 600 秒预算后超时，已保存的模型文件经过单独打包恢复；原失败保留，恢复不调用模型。场景自测九项通过，只说明脚本与方法包可运行，不构成跨任务效率证据。[恢复与成本记录](results/skill-capture-recovery.json)。
