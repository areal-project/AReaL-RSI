# 示例目录

示例按任务分类。第一次使用可以从 [goal.json](goal.json) 开始：由一个 Leader 编写并测试
CSV 汇总脚本，只需要研究模型。验证“评测 → 经验 → 改进 → 再评测”的完整循环，
使用 [单题 Math loop](harness/math_loop/README.md)，默认同一模型连接、至少三个 harness 版本。
需要体验独立 VALIDATE 评测时，使用 [算术 harness](harness/README.md#算术-harness)，
它额外需要一个固定的目标模型，不需要 GPU、SSH 或 EnvArena。

| 入口 | 类型 | 优化对象 | 额外依赖 | 用途 |
| --- | --- | --- | --- | --- |
| [Math loop](harness/math_loop/README.md) | Local E2E | 三版数学 harness 与反馈循环 | 默认仅需研究模型连接 | 单题、两轮改进、最终复测与结构化验收 |
| [`goal.json`](goal.json) | Quickstart | CSV 脚本、测试和说明 | 无 | 单 Leader goal 与基于证据的验收 |
| [Arithmetic](harness/README.md#算术-harness) | Benchmark | 算术 Task harness 的 prompt/skill | 一个目标模型连接 | 候选、TRAIN 评测与 VALIDATE 验证 |
| [LiveCodeBench](harness/README.md#livecodebench-harness) | Benchmark | 通用 system prompt | EnvArena 账号与固定 Benchmark | 远端评测接入 |
| [`harness/kernelbench/`](harness/kernelbench/README.md) | Advanced benchmark | 通用 KernelBench harness | KernelBench 数据与 GPU 评测环境 | 研究可复用 harness |
| [`runtime/`](runtime/) | Configuration | Agent Runtime profile | 对应 Runtime 或远端服务 | 选择 Agent 后端 |
| [`scale_rsi_bench/`](scale_rsi_bench/README.md) | Deployment | 多种 Scale RSI Bench 任务 | Slurm、容器和任务专用环境 | 集群部署与复现实验 |
| [`gsm8k/`](gsm8k/README.md) | Component | GSM8K 数据快照 | 数据下载权限 | 为自定义 Benchmark 准备数据 |

## 运行方式

在仓库根目录配置 `.env` 后，加载单 agent 示例，在 TUI 中输入 `/start`：

```bash
uv run --env-file .env --locked rsi tui --config examples/goal.json
```

Harness 示例先生成可信 `ResearchConfig`，随后交给同一个 `ResearchSystem`：

```text
准备可信任务配置
  → rsi tui --config ... 或 rsi research --config ...
  → Leader 执行 goal
  → 保存候选，执行配置的 search/final 验证
  → Leader 检查证据并接受本 run
```

示例负责固定任务、评测和运行环境，不实现另一套研究编排器。默认不创建 Researcher。
需要独立完整尝试时显式使用 [legacy pass@k](../docs/passk.md)；未来 Agent Team 尚未实现。
旧 `local/`、`remote/` 和 `kernelbench/` 的 harness 示例已经归入 `harness/`，
`kernelbench_artifacts/` 的个人实验与启动器已删除。

Runtime profile 是后端配置，不是独立 Benchmark，也不代表对应后端已经完成端到端验收。
配置方法见 [Agent 后端](../docs/agent-backends.md)；部署与数据组件各自的环境要求见对应指南。
