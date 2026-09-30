# AReaL RSI

从空历史重建的模型与 Harness RSI 实验项目。首个目标是数学 TIR Agent 的单代闭环：冻结任务与 `M0/H0`，搜索并选择 `H1`，在固定 `H1` 下用 AReaL GRPO 训练 `M1`，配对评测四种组合并记录发布决定。

当前已有从候选生成/选版到 AReaL 2.0 GRPO recipe、配对评测与原子发布的代码和 CPU 行为测试。**尚未在 Slurm GPU 集群执行真实推理、训练或封存评测**；CPU 模拟结果不能代表能力提升。运行准备见 [Slurm TIR 示例](examples/areal_rsi_tir/README.md)，验收事实见 [实现状态](docs/implementation-status.md)，当前约束见 [架构](docs/architecture.md) 与 [研发计划](docs/roadmap.md)。完整的[原始设计方案](docs/model-harness-rsi-design.md)和[原始研发计划](docs/model-harness-rsi-development-plan.md)已恢复为历史参考。旧项目代码仍留在原分支。

首版只支持 Slurm：控制器运行在能调用 `sbatch` 的集群环境，AReaL 负责提交 GPU worker。数据合成和 SFT 是后续独立实验，不进入首个闭环的关键路径。
