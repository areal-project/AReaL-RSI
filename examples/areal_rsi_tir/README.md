# Slurm TIR RSI 示例

此目录给出 AReaL 2.0 recipe 的配置形状。已在现有 Slurm allocation 内以 local scheduler 完成真实 ToRL RSI 首代闭环：2048 题、3 epoch、192 次 GRPO 更新及四组 dev/sealed 评测，M1H1 通过预登记发布门槛，证据见 [验收状态](../../docs/implementation-status.md)。控制器、AReaL worker、模型和文件系统必须在目标 Slurm 集群联合预检。首版 GRPO 需要 AReaL 代码中带 `source_task_id` 的 rollout dump 和严格的 TIR reward 超时处理。

## 准备

在已有 Slurm allocation 内做单机调试时，可通过 `srun --jobid=<job-id> --overlap --mpi=none` 进入节点，在 AReaL 镜像内直接运行 `areal_rsi_tir.evaluate` 或 `areal_rsi_tir.train_rl`，覆盖 `scheduler.type=local`，并设置 `CUDA_VISIBLE_DEVICES` 限定使用的 GPU。recipe 使用 AReaL `LocalScheduler` 创建本机 worker，无需另外申请 Slurm 作业。该入口用于已分配节点上的测试；`cycle run` 的自动部署及作业收据仍要求 Slurm 调度证据。

在同一个集群共享路径上安装本包和相邻的 AReaL checkout。运行控制器的环境需要 `sbatch`；GPU worker 应能导入版本一致的 `areal_rsi`、`areal`、`examples.tir`，能读模型、任务/Harness 快照及共享的 `cluster.fileroot` 和 name-resolve 根目录。`tir_grpo.yaml` 从 AReaL TIR 配方取初始参数，已固定 `scheduler.type: slurm`、计算器工具；根据实际 Slurm 节点修改 GPU 数、batch 和并发。AReaL 当前 Slurm 调度器按整节点申请 GPU。

从公开 ToRL parquet 生成去重后的规范 JSONL，再冻结互斥的五组题：

```bash
python -m areal_rsi_tir.prepare_torl \
  --train /shared/source/train.parquet --test /shared/source/test.parquet \
  --output /shared/rsi/converted-v1

python -m areal_rsi_tir.tasks \
  --train /shared/rsi/converted-v1/private/train.jsonl \
  --test /shared/rsi/converted-v1/private/test.jsonl \
  --output /shared/rsi/taskset-v1 \
  --harness-evolve 256 --harness-select 128 \
  --model-train 2048 --model-dev 256 --seed 42

python -m areal_rsi_tir.harness \
  --prompt /shared/AReaL-RSI/examples/areal_rsi_tir/h0_prompt.txt \
  --runner-root /shared/AReaL/examples/tir \
  --output /shared/rsi/harness-h0

python -m areal_rsi_tir.assets \
  --checkpoint /shared/models/Qwen2.5-Math-1.5B
```

复制 [generation.example.json](generation.example.json)，替换所有路径、模型接口与预算。若已由外部研究员提供通用 prompt，可先写到 `candidate_prompt` 路径并把 `researcher` 设为 `null`；默认路径会先用 `harness_evolve` 的真实失败反馈请求研究员模型生成 prompt。研究员只见公开题面、模型回答和分数，不见私有答案、选择集或封存集。目标模型必须是本地可读的完整快照；把模型名写在 `actor.path` 不构成冻结身份。

```bash
python -m areal_rsi_tir.cycle run \
  --data-root /shared/rsi/experiments \
  --config /shared/rsi/generation.json
```

控制器先检查 `sbatch/squeue/scancel/srun/scontrol` 和共享目录可写性，再通过 AReaL 2.0 的 `eval_tir` 和 `train_tir_rl` recipe 执行。每个外部阶段在 `runs/<run-id>/jobs/` 留下命令意图、日志和结果摘要；`runs/<run-id>/reports/` 保存逐题分数及对应的 token/mask 轨迹证据。Harness 选择前重复一次 H0 并记录 A/A 波动。若控制器在 recipe 写出最终结果后中断，重跑会根据相同命令意图、结果摘要和 Slurm job ID 恢复收据；若结果缺失则停止，需先核对或取消原 Slurm 作业，不会自动重复提交。恢复收据的耗时字段为未知。成功时 `releases/` 写入内容寻址版本，`active.json` 原子切换。无增益、奖励方差不足或封存不达标时写明拒绝结果。数据合成与审查轨迹 SFT 均不属于首版运行路径。

当外部作业状态不确定时，读取 `runs/<run-id>/jobs/<stage>.log` 中的 Slurm job ID，用 `squeue`/`sacct` 核对对应作业及共享路径上的最终输出；结果缺失时保持停止状态。

训练 recipe 在启动前写入不可变的 `*.intent.json`，保存题集、Harness、模型、配置和源码摘要。若 AReaL 已保存模型及 rollout，但 recipe 在写收据时失败，核实原 Slurm 作业已经结束后，以原命令、原 `run-id`、原配置和原收据路径重运行训练 recipe，并加 `--reconcile-only`。该模式要求当前输入与原意图完全一致，只校验现有产物并生成收据，不重复训练。首个 AReaL 检查点路径以零基索引命名为 `globalstep0`，收据中的已完成步数为 1。

若已发布版本随后需要回滚，提供当前版本 ID 和审计原因；命令会核对当前指针并记录回滚事件：

```bash
python -m areal_rsi_tir.cycle rollback \
  --data-root /shared/rsi/experiments --run-id tir_demo_001 \
  --expected-release-id release_ACTUAL_ID --reason "observed regression"
```

本地 CPU 可用 `--validate-only` 分别检查训练与评测 recipe 的冻结身份和 Slurm 配置解析；这不会调用模型、`sbatch` 或训练器。项目测试覆盖控制流程与故障路径，不能替代真实集群验收。
