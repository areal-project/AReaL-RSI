# Slurm TIR RSI 示例

此目录只保留数学任务的 profile、提示词与运行配置样例。共享代际流程在 `infra/coordination/generation.py`，AReaL 的 trainer 和 worker 生命周期在 `scripts/train.py`。所有入口只支持 Slurm，actor 与 rollout 使用 v2。

在控制器与 GPU worker 的同一共享路径安装 `areal_rsi` 和 AReaL checkout。worker 的 `PYTHONPATH` 须包括 AReaL checkout 和本仓库的 `src`，以同时导入 `areal`、AReaL 的 `examples.tir` 和完整 RSI 包；不再需要仓库顶层 `examples`。站点配置负责镜像、Slurm/munge 挂载、分区、reservation 与 GPU 数。

```bash
python -m areal_rsi.infra.coordination.evaluation.tir.prepare_torl \
  --train /shared/source/train.parquet --test /shared/source/test.parquet \
  --output /shared/rsi/converted-v1

python -m areal_rsi.infra.coordination.evaluation.tasks \
  --train /shared/rsi/converted-v1/private/train.jsonl \
  --test /shared/rsi/converted-v1/private/test.jsonl --output /shared/rsi/taskset-v1 \
  --harness-evolve 256 --harness-select 128 --model-train 2048 --model-dev 256 --seed 42

python -m areal_rsi.infra.coordination.evaluation.harness \
  --prompt src/areal_rsi/examples/tir/h0_prompt.txt \
  --runner-root /shared/AReaL/examples/tir --tools calculator --output /shared/rsi/harness-h0

python -m areal_rsi.examples.tir.cycle run \
  --data-root /shared/rsi/experiments --config /shared/rsi/generation.json
```

复制 [generation.example.json](generation.example.json)，替换共享路径、完整模型快照、研究员接口和预算。公共 [grpo.yaml](../grpo.yaml)负责 AReaL 资源与优化参数；cycle 从冻结 Harness 添加 TIR 工具设置，并强制 Slurm/v2。同一个配置文件可以供普通 RLVR 分类任务使用。

研究员只见 `harness_evolve` 的公开题面、模型回答和分数。每个 run 冻结 `profile.json` 与配置；作业意图、日志和收据保存在 `runs/<run_id>/jobs/`，报告及 token/mask 证据保存在 `reports/`。恢复时拒绝变化的 profile、配置、题集、源码或结果。缺失最终结果的作业必须先核对已有 Slurm job，避免重复提交。

若训练已产出检查点与 rollout、但尚未写完收据，核实原作业结束后，按原命令执行公共训练入口并添加 `--reconcile-only`：

```bash
python -m areal_rsi.scripts.train \
  --recipe areal_rsi.infra.coordination.evaluation.train.prepare \
  --profile /shared/rsi/experiments/runs/RUN_ID/profile.json \
  --task-set-root /shared/rsi/taskset-v1 --harness-root /shared/rsi/harness-h0 \
  --base-model-digest sha256:MODEL_DIGEST --run-id RUN_ID --receipt /shared/rsi/train.json \
  --config /shared/rsi/experiments/runs/RUN_ID/areal_config.yaml \
  ++tir.enable_tools=calculator --reconcile-only
```

以上只展示入口形状；恢复必须保留原日志中的全部身份及资源参数。`--validate-only` 只解析配置和冻结身份。

```bash
python -m areal_rsi.examples.tir.cycle rollback \
  --data-root /shared/rsi/experiments --run-id RUN_ID \
  --expected-release-id release_ACTUAL_ID --reason "observed regression"
```

普通分类任务配置参见 `areal_rsi.examples.classification.PROFILE`：冻结题集格式相同，Harness 使用 AReaL 的 `areal/` 作为 runner root 和 `--tools none`，然后用 `areal-rsi-classification run` 执行同一代际流程。新模型只需修改运行配置的 `model_root` 和 AReaL 资源参数。

Claude 数学求解、合成数据校准和连续实验实现已移入 infra；历史部署脚本位于 `infra/coordination/launch/`。其站点路径与历史实验预算仍需按实验检查。新训练统一通过任务无关的 `scripts/train.py --recipe ...`，历史记录不迁移或改写。
