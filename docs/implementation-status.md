# 实现与验收状态

更新：2026-09-28。以下区分代码存在、CPU 实测与目标 Slurm GPU 验收。

| 阶段 | 当前事实 | 尚需证据 |
| --- | --- | --- |
| P0 数据和契约 | 新分支实现 ToRL parquet 转换、去重谱系、五组题集、不可变 Harness 与摘要校验。本机已有 ToRL 快照实测 28,740/374 原始 train/test 行，去重后 28,738/194 题，并冻结 256/128/2048/256/194 五组。 | 源数据许可证和目标集群共享路径核验。 |
| P1 执行一致性 | AReaL TIR 训练/评测 recipe 共用冻结 prompt、计算器设置和严格奖励；worker 创建 workflow 时核对 AReaL/RSI 源码、题集/Harness 和模型摘要。AReaL 2.0 配置解析与 `--validate-only` 在本机通过；该检查使用极小的占位模型目录，只验证配置和摘要。 | 目标 Qwen 检查点/tokenizer、真实 Slurm worker 上的摘要检查、SGLang 与 `M0H0` rollout；A/A 波动。 |
| P2 Harness 搜索 | 控制器从 `harness_evolve` 的公开题、回答和分数生成一个候选，经 `harness_select` 独立选版；反馈边界有 CPU 测试。 | 使用真实模型失败轨迹运行研究员，冻结至少一个有效 H1 和选择回执；测真实成本。 |
| P3 GRPO | AReaL 2.0 `PPOTrainer` recipe、训练前奖励方差门槛、Slurm 作业意图、rollout/检查点收据及重入保护已实现。 | Slurm 提交记录、至少一个真实训练 step、完整模型检查点与覆盖全部训练题的 rollout dump。 |
| P4 评测发布 | 2×2 dev/sealed 决策、配对 bootstrap、内容寻址发布、原子指针、回滚和第二代封存题隔离有 CPU 行为测试。 | 真实逐题四组评测、一次预注册的 sealed 决策、回滚后的真实推理。 |

本机验证：`make check` 通过 Ruff、Mypy 和 12 个 CPU 测试；`uv build` 生成 sdist/wheel；AReaL 训练与评测脚本各通过一次 `--validate-only`。`FrozenTIRWorkflow` 用一个极小 tokenizer 在 AReaL Python 环境完成 CPU 构造和来源校验，启用工具仅为 `calculator`；篡改预期模型摘要时构造失败。本机无 GPU，以上**不是**端到端训练成功或泛化提升的证据。真实集群验收后应记录 AReaL/RSI commit、模型/Harness/任务摘要、Slurm job ID、日志、检查点、rollout、四组逐题报告和发布决定。
