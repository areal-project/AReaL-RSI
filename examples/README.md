# 示例目录

示例按用户要完成的任务分类。第一次使用请从
[Arithmetic quickstart](local/README.md) 开始；它覆盖配置、研究、候选验证和独立最终复测，
不需要 GPU、SSH 或 EnvArena。

| 目录 | 类型 | 优化对象 | 额外依赖 | 建议用途 |
| --- | --- | --- | --- | --- |
| [`local/`](local/README.md) | Quickstart | 算术 Task harness 的 prompt/skill | 一个被测模型连接 | 第一次完整运行 |
| [`remote/`](remote/README.md) | Benchmark | LiveCodeBench system prompt | EnvArena 账号与固定 Benchmark | 远端评测接入 |
| [`kernelbench/`](kernelbench/README.md) | Advanced benchmark | 通用 KernelBench harness | KernelBench 数据、SSH/GPU 环境 | 研究可复用 harness |
| [`kernelbench_artifacts/`](kernelbench_artifacts/README.md) | Advanced benchmark | 指定 KernelBench 题目的 kernel 代码 | 已部署的 GPUQ/et15 环境 | 单题 pass@k 研究 |
| [`runtime/`](runtime/) | Configuration | Agent Runtime profile | 对应 Runtime 或远端服务 | 选择和组合 Agent 后端 |
| [`scale_rsi_bench/`](scale_rsi_bench/README.md) | Deployment | 多种 Scale RSI Bench 任务 | Slurm、容器和任务专用环境 | 集群部署与复现实验 |
| [`gsm8k/`](gsm8k/README.md) | Component | GSM8K 数据快照 | 数据下载权限 | 为自定义 Benchmark 准备数据 |

## 共同运行模型

可运行的研究示例都生成一个 `ResearchConfig`，随后交给同一个 ResearchApp 入口：

```text
准备可信任务配置
  → rsi research --config ...
  → ResearchApp 启动 Agent
  → 保存并验证候选
  → 独立最终复测
```

示例目录只负责固定任务、评测和运行环境，不实现另一套研究编排器。模型后端、协作方式、
恢复和工作台展示仍由 ResearchApp 管理。

## 如何选择

- 想确认安装和基本研究流程：使用 Arithmetic。
- 已有 EnvArena Benchmark：使用 LiveCodeBench 接入作为参考。
- 优化跨题通用的 GPU harness：使用 `kernelbench/`。
- 优化一条具体 kernel：使用 `kernelbench_artifacts/`。
- 只想切换 Hermes、Claude、Codex 或混合角色后端：读取 `runtime/` 配置和
  [Runtime 文档](../docs/agent-backends.md)，不要把它当作独立 Benchmark。
- 部署 Slurm 训练任务：从 `scale_rsi_bench/` 的环境检查开始。

每个 Benchmark README 都应明确说明优化对象、可信评测边界、外部依赖、启动命令、恢复方式
和结果位置。目录中只有配置或数据工具时，会明确标为 Configuration 或 Component。
