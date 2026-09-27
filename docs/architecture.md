# 最小 RSI 闭环架构

状态：设计草案，2026-09-28。本文定义要实现和验收的行为，不代表已有 GPU 实测。

## 1. 目标与实验单位

第一版任务是 ToRL 数学题上的 TIR 计算器 Agent。模型初始值为 Qwen2.5-Math-1.5B；这个选择是运行假设，必须在目标集群预检模型权重、tokenizer、SGLang 和显存。Harness 首版只允许修改非聊天 TIR 路径实际使用的通用 prompt、工具说明与有限的采样预算。只启用受限计算器，不启用本地 Python 执行器。

一个 `generation` 的输入是不可变的 `TaskSet`、`M0` 检查点、`H0`、AReaL/TIR 源码摘要、验证器摘要、预算与随机种子。输出是一条可重放的决策：`H1`、训练收据 `M1`、四组评测、晋升或拒绝及下一代起点。必须能在没有提升时完整结束，并明确记录拒绝原因。

```mermaid
flowchart LR
  A[冻结 TaskSet 与 M0/H0] --> B[基线 M0H0]
  B --> C[研究员搜索 Harness 候选]
  C --> D[独立选择 H1]
  D --> E[预检 RL 奖励方差]
  E --> F[固定 H1 的 AReaL GRPO]
  F --> G[验证 M1 检查点与轨迹]
  G --> H[同题同预算的 2×2 评测]
  H --> I[封存集决策]
  I --> J[原子晋升或保留旧版]
  J --> K[下一代读取已发布指针]
```

## 2. 隔离与数据契约

原始 ToRL 数据经一次转换、去重后冻结为五组互斥题：`harness_evolve`、`harness_select`、`model_train`、`model_dev`、`sealed_test`。每题有稳定 ID；公开题面和私有答案分文件保存。各组源文件、题 ID、答案与 split manifest 均有内容摘要。训练或研究员输入不能包含 `sealed_test`，研究员不能读 `harness_select` 的私有答案。官方测试题的身份及许可证在冻结前核查。

`H0/H1` 是不可变快照：prompt 字节、可改字段、TIR runner 源码摘要、工具集合与预算。每个训练/eval worker 在加载时检查同一快照和源码摘要；检查失败则停止，不自动退回默认 prompt。研究员只在隔离工作区提出候选，不能改任务、答案、验证器、工具实现、训练配置或源码锁。首版默认一个研究员；候选数量、查询次数、token/GPU 预算在运行前固定。研究员看 `harness_evolve` 的逐题反馈，候选冻结后才由选择器读取 `harness_select` 一次。选择结果不再回流给同代研究员继续搜索。

`M1` 必须是训练作业产生且能重新加载的完整检查点，收据绑定 `M0/H1/TaskSet/runner/verifier/config` 摘要、训练 step、rollout 来源及 Slurm job ID。作业提交、取消、超时与结果回收是显式状态；重试先核对 Slurm/job/checkpoint/rollout，不重复提交同一个训练身份。

## 3. 模块边界

| 位置 | 职责 |
| --- | --- |
| `src/areal_rsi/api/records.py` | TaskSet、Harness、模型、评测和发布的具名不可变记录 |
| `src/areal_rsi/api/research.py` | 单代实验规格、预算、状态与决策契约 |
| `src/areal_rsi/api/execution.py` | 外部作业与轨迹收据 |
| `src/areal_rsi/api/control.py` | JSON 边界解码、ID/路径校验、控制请求 |
| `src/areal_rsi/infra/research.py` | 唯一系统入口 `ResearchSystem`；检查状态与授权，推进 generation |
| `src/areal_rsi/infra/tasks/` | ToRL 冻结、TIR Harness 快照、私有验证器 |
| `src/areal_rsi/infra/coordination/` | 状态、作业记录、原子发布；`policies/` 选版，`evaluation/` 配对分析，`tools/` 研究员可见能力 |
| `src/areal_rsi/infra/training/` | AReaL 2.0 GRPO recipe 与只支持 Slurm 的集群预检 |

API 不依赖 infra；infra 不依赖 UI。系统不复制 AReaL 的调度器或训练器。AReaL recipe 直接用它的 `PPOTrainer`、`TIRWorkflow` 和 `SlurmScheduler`；RSI 管实验身份、证据及晋升规则。当前旧分支的实现可作为参考，但代码要按上述契约重新选择和整理，不整包搬迁旧 `ResearchSystem`、Web UI、pass@k 控制层或 Ray 适配。

旧历史前十个 commit 中，首个 API commit 的 `Task`、`VerificationResult`、`ModelVersion`、`HarnessVersion`、`TrajectoryRef` 等**值对象思路**值得保留；新契约会围绕一个真实 TIR 训练闭环重新命名和收窄。`ContentStore/Memory/AgentRuntime/TrainingBackend/Benchmark` 等大量单实现 `Protocol`、通用 `EvolutionTarget` 和旧服务接口不作为兼容目标。需要第二个实际实现时，再从调用点提取最小协议。这里保留的是可检验的语义，不承诺兼容旧 JSON schema 或 import 路径。

## 4. Slurm 部署

控制器与研究员运行在可调用 `sbatch` 的 Linux 集群入口；GPU actor、rollout 和评测 worker 由 AReaL `scheduler.type: slurm` 分配。AReaL 当前 SlurmScheduler 在本机执行 `sbatch` 并生成带 `srun` 的 worker 脚本，因此不把 Mac→Ray 远端拓扑列为首版目标。集群提供 worker 可读的模型、题集、Harness、源码锁及同版本 Python 环境，和共享的实验、checkpoint、name-resolve 目录。集群账号、分区、节点/GPU 布局和文件系统路径由部署配置给出，不写进源码。

AReaL 当前 Slurm 提交器要求按整节点分配 GPU；最小烟测的实际资源需求取决于目标分区每节点的 GPU 数。先测出可用节点形状，再冻结 batch、并发和训练预算。

预检分两段：控制器验证 `sbatch/squeue/scancel`、共享路径与 recipe 配置；一个实际 Slurm worker 验证 GPU、SGLang、模型加载、源码/任务/Harness 摘要及最小 TIR rollout。只有真实 worker 通过，才能提交完整训练。已有 AReaL 配置示例使用 `scheduler.type: slurm`；最终命令必须在目标集群验证。

## 5. 训练、比较和晋升

首版采用 GRPO，因为 TIR 有在线采样与可计算奖励，能直接检验 Harness 改动后的采样和模型更新。训练前用固定样本测同题多次采样的 0/1 混合比例；若奖励全常数、验证器异常或工具失败率超界，实验停在预检状态，不制造“训练成功”的假象。SFT 作为后续对照：从有来源、无私有答案泄漏的审查轨迹构建训练集，按相同评测门槛比较；不能预先宣称 RL 泛化更好。

评测在 `model_dev` 上先跑 `M0H0`、`M0H1`、`M1H0`、`M1H1`，同题、同 seed 集、同生成/token/工具预算；保存逐题轨迹、验证结果、token/工具/时间/GPU 成本，并重复基线以估计噪声。锁定候选模型、Harness、阈值与分析代码后，才进行一次 `sealed_test`。晋升规则至少检查主目标的配对提升及不确定区间、成本上限、无重大子集退化和执行完整性。主目标可配置为联合性能或模型内化；两者的判断必须分别报告，避免只靠 `M1H1` 的提升声称模型自身进步。发布指针原子更新，保留父版本，回滚有记录；下一代仅从发布指针读取 `(M,H)`。

## 6. 必须验证的故障路径

- 重复题/跨 split 泄漏、答案进入公开文件、冻结文件或 runner 改动。
- 候选越权修改、候选搜索后篡改、选择集被反复查询。
- 验证器超时/异常、工具失败、全零或全一奖励。
- `sbatch` 成功后控制器崩溃、作业完成后回执丢失、同一训练身份重试。
- 不完整/不可加载检查点、rollout 与题 ID 不一致、训练/评测执行器摘要不一致。
- 封存门槛未锁定、重复打开 sealed split、发布中断与回滚。

CPU 模拟测试只能验证控制逻辑和故障处理。P3/P4 完成标准必须包含目标 Slurm 集群上的真实模型调用、至少一个训练 step、可加载 checkpoint 与真实封存集报告。
