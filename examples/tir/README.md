# 独立 TIR 训练示例

本例独立执行“合成数据 → 基线评测 → AReaL GRPO → checkpoint → 复评 → 对比”。
不启动 Codex、TUI 或 `/rsi`，也不修改现有会话流程。默认是验证接入的两步训练，
不代表充分训练或质量收益实验。

原生 ToRL 的多轮训练、冻结评测和 `/rsi` 驱动研究见 [RESEARCH.md](RESEARCH.md)。

多轮研究现在支持三种互相衔接的数据阶段：

- `experiment probe` 用当前 checkpoint 对训练池每题重复采样；`dynamic_curriculum`
  根据同题的正确与错误回答选题，同时保留随机真实题。训练前会核对 probe 对应的模型，
  warm start 模型变化后需要重新试答。
- `distillation.py` 让教师先生成推理和 Python 程序，使用后端真实工具执行后，再请求最终答案。
  教师请求不包含 gold；只有最终回答通过数学答案校验、工具成功且长度合规的轨迹能进入 SFT。
  教师生成可在 CPU 的 AReaL 环境运行；凭证从环境读取，不写入配置与轨迹。
- `compositional.py` 生成不同数字约束、带必经点和障碍的路径、条件抽样概率题，每题通过
  两种独立的精确算法验算。`admit_compositional` 再筛选当前学生有对有错的题，
  混入比例受限于至少十道真实题配一道合成题。

`RoundRecipe.algorithm` 为 `sft` 时，`data` 为 `null`，`distillation` 引用已验证的教师 manifest；
为 `grpo` 时使用 `DataRecipe`，可通过 `curriculum` 和 `synthetic_admission` 引用上述筛选记录。
SFT 在训练节点用实际 parent 的 tokenizer 重建 token 和 mask；题目及真实工具观察不参与 loss，
仅学习教师生成内容。通用的 token 契约、SFT 执行、权重变化检查和 checkpoint 校验位于
`api/execution.py` 与 `infra/training.py`；任务选题、合成和训练阶段编排保留在本例。

`ExperimentConfig.target_accuracy` 可额外指定 test 的绝对准确率目标，例如 `0.37` 表示必须
严格超过 37%，并且仍要满足 `target_delta` 的相对提升要求。模型选择继续只使用 valid。

## 环境与运行

提交端按仓库 README 准备 `uv` 环境。训练使用独立的 AReaL Python 环境，需能导入
所选 AReaL 源码及其 TIR 依赖（PyTorch、SGLang、datasets、Qwen-Agent 等）。
无需把这些 GPU 依赖装入 RSI 的 `.venv`。

`config.json` 提供集群运行配置示例；启动前必须将 `runtime.repository` 改为实际的 AReaL 源码目录，并核对模型、Python 和镜像路径：

- 后端：`/path/to/AReaL`（占位路径，运行前替换）。
- 模型：`/storage/openpsi/models/Qwen__Qwen2.5-Math-1.5B`。
- GPU Python：容器内 `/opt/.venv/bin/python`。
- 镜像：`/storage/openpsi/images/areal-dev-sglang-0821.sif`。

从仓库根目录运行一条命令提交完整实验：

```bash
bash examples/tir/run_slurm.sh
```

脚本返回 Slurm job ID，在 `rsi` reservation、`all` 分区申请单节点两张 GPU、
16 CPU、128 GiB 内存。整个 pipeline 在同一个 allocation 的容器内运行；
AReaL 使用 v1、本地 scheduler，在 allocation 内启动 actor 和 rollout worker。
不另行申请未指定 reservation 的 GPU worker。

```bash
squeue -j <job-id>
sacct -j <job-id> --format=JobID,State,ExitCode,Elapsed,NodeList -X
tail -f runs/tir-slurm/slurm-<job-id>.log
```

修改配置后提交：

```bash
bash examples/tir/run_slurm.sh /absolute/path/to/config.json
```

`TIR_IMAGE`、`TIR_SUBMIT_ROOT`、`TIR_TIME_LIMIT` 可覆盖镜像、提交日志目录和 Slurm 时限；
`TIR_RESERVATION` 默认 `rsi`。这里只取消自己提交的精确 job ID：
`scancel <job-id>`。不要按共享用户批量取消作业。

在已分配两张 GPU、安装好 AReaL 的环境中，可直接执行同一入口：

```bash
uv run --locked python -m examples.tir.pipeline --config examples/tir/config.json
```

这时 `runtime.python` 应指向实际的 AReaL Python，`runtime.repository`、`model`、
`output_root` 按本机修改。所有相对路径相对于启动工作目录；建议从仓库根目录启动。
主机上的 `/opt/.venv/bin/python` 不存在时，不能直接使用容器专用的默认配置。

仅检查数据与输入记录，不调用 AReaL、模型或 GPU：

```bash
uv run --locked python -m examples.tir.pipeline \
  --config examples/tir/config.json --prepare-only
```

此命令读取本地模型文件并计算摘要，最终状态为 `prepared`，不产生训练或评测成绩。

## 实验与配置

`data.py` 使用种子生成整数算术、多项式求和与模运算计数题。
答案经独立计算方法验证，题目按内容 ID 去重，再拆成互不重叠的 train/valid/test。
默认规模 32/8/8；valid 用于训练阶段的验证，test 用于固定的训练前后评测。
各 split 使用独立随机流，增加训练题目数不会改变已固定的 test/valid 集。
合成过程不读取 test 的回答或轨迹。

`workflow.py` 复用后端 `examples/tir/tir_workflow.py` 的多轮生成与工具执行，
额外记录实际 prompt、生成片段、工具调用及工具返回。`reward.py` 只对本例
整数答案计算奖励，使用最后一个 `\boxed{整数}`。错误回答记零分；执行异常使任务失败，
不混入零分回答。Python 工具沿用 AReaL 的研究执行环境，不是隔离沙箱。

训练使用在线 GRPO：输入是题目和答案；token、logprob、工具输出的 loss mask、
权重版本与权重同步由 AReaL 管理。独立评测轨迹不直接作为 GRPO rollout 输入。

配置字段：

| 字段 | 用途 |
| --- | --- |
| `runtime` | 后端源码、Python、子进程环境变量 |
| `model` / `output_root` | 基线 HF 模型目录 / 实验根目录 |
| `synthesis` | 随机种子、三个 split 的规模 |
| `harness` | 工具列表、最大轮次、上下文长度、工具超时、chat 模式 |
| `training` | epochs、batch_size、n_samples、max_steps、生成参数、学习率 |
| `evaluation` | 固定的评测种子、生成长度和 temperature（默认 greedy） |
| `timeout_seconds` | 每个评测／训练子任务的执行超时 |
| `backend_config` / `backend_overrides` | AReaL YAML 模板及 Hydra dotlist 参数 |

例子中的固定运行设置（独立输出路径、本地调度、模型、harness、数据路径、保存周期等）
会覆盖模板及 overrides；每个 job 的 `areal-config.yaml` 保存最终配置。
增加 `max_steps` 时还需保证 `epochs × 每轮 batch 数` 足够。
基线和复评共享 test 数据、harness、生成参数和种子，只有模型路径不同。
随机种子不承诺跨硬件／库版本逐 token 完全一致。

## 产物与故障

每次执行生成唯一目录 `runs/tir/tir-<时间>-<随机 ID>/`，旧目录不会覆盖或自动恢复。
相同配置可复现数据，运行 ID 和工作产物保持独立。

| 产物 | 内容 |
| --- | --- |
| `experiment.json` | 阶段、结果引用、失败／取消原因 |
| `config.json`、`data/manifest.json` | 实际配置、split 摘要与路径 |
| `source-*.json`、`source-*.tar.gz` | 两个项目的源码清单、实际源文件归档（含未提交修改） |
| `base-model.json`、`harness.json` | 模型文件及后端 TIR 文件的 SHA-256 清单 |
| `jobs/<阶段>/job.json`、`stdout.log` | 命令、生命周期、退出码、日志 |
| `jobs/<阶段>/samples.jsonl`、`traces/` | 逐题结果及完整交互轨迹 |
| `trajectories/` | 按 task/solution 可查询的轨迹索引 |
| `jobs/train/areal/` | AReaL 的训练日志、指标与 HF checkpoint |
| `jobs/train/training.json`、`checkpoint.json`、`result.json` | 实际保存步数、参数变化探针、checkpoint 文件清单及引用 |
| `comparison.json`、`report.md` | 前后指标、配对统计、全部证据关联 |

只有所有题目均返回、训练至少完成一步、新 checkpoint 校验通过并可重载评测、源码在
实验期间未变化时，状态才是 `completed`。分数没有上升也可完成闭环；报告训练前后的配对准确率变化，不根据 test 分数选择模型。
多轮候选选择由 `experiment.py` 使用 valid 完成。源码发生变化会拒绝发布对比结果，应保留该失败记录并用新运行重试。

每个阶段失败会立即停止 pipeline，先查看 `experiment.json` 和相应 `stdout.log`，
再看 `areal/logs/` 下的 actor／rollout 日志。评测缺题、重复题、推理异常、缺失分片和
未发生参数变化都不会作为成功返回。超时或 Ctrl+C 会记录终态并清理本次子任务的进程树；
强制 SIGKILL、节点丢失等无法执行清理的情况仍需结合 Slurm 作业状态核查。

轨迹可以通过 `LocalTrajectoryStore.query(TrajectoryQuery(task_id=..., solution_id=...))`
查询。评测服务可单独使用，不依赖训练服务，因此其他任务只修改 harness 时也能复用。

## 开发验证

```bash
uv run --locked pytest tests/test_training.py
make check
```

离线测试执行真实子进程，检查数据、失败、超时、中断、评测完整性和产物校验。
测试中的小文件只用于验证产物契约，不是训练 checkpoint，也不提供训练收益证据。
GPU 验证必须另行检查实际 Slurm job、训练 step、非零参数变化、权重保存／重载和评测产物。
