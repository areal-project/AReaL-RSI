# AReaL RSI

从空历史重建的模型与 Harness RSI 实验项目。首个目标是数学 TIR Agent 的单代闭环：冻结任务与 `M0/H0`，搜索并选择 `H1`，在固定 `H1` 下用 AReaL GRPO 训练 `M1`，配对评测四种组合，再决定晋升或回滚。

项目当前处于**设计阶段**。这个分支还没有可运行的训练闭环或 GPU 结果。设计见 [架构](docs/architecture.md) 与 [研发计划](docs/roadmap.md)。旧项目保留在原分支作为实现参考，不属于本分支历史。

首版只支持 Slurm：控制器运行在能调用 `sbatch` 的集群环境，AReaL 负责提交 GPU worker。数据合成和 SFT 是后续独立实验，不进入首个闭环的关键路径。
