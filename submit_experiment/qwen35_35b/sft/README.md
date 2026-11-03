# Qwen3.5-35B-A3B SFT

通过 Slurm 申请一个 8-GPU 作业，在既有 actor 镜像内运行
`torchrun --standalone --nnodes=1 --nproc-per-node=8`。
`AREAL_SPMD_MODE=1`、`scheduler.type=null`，各 rank 直接创建训练引擎；
资源仍由 `sbatch` / `srun` 管理，不创建独立 CPU controller 或 RPC workers。

## 默认配置

| 项目 | 默认值 |
| --- | --- |
| 模型 | `/storage/openpsi/models/Qwen__Qwen3.5-35B-A3B`，全参数 SFT |
| 并行 | Attention DP2 × CP4、TP1/PP1；FFN EP8/ETP1/EDP1 |
| Slurm 资源 | 单机 8 GPU、64 CPU、`--mem=1200G`，24 小时 |
| 最大序列长度 | 262144（256K）；不将短样本补至 256K |
| Global batch | 32，即每个 attention DP 分片 16 条 |
| 微批 | 不显式设置 `n_mbs`；`n_mbs_divisor=1`、`max_tokens_per_mb=262144` |
| 训练长度 | 1 epoch；`total_train_steps=null`，可设置步数上限 |
| AdamW | `type=adam`，LR `2e-5`，betas `0.9/0.95`，eps `1e-8`，weight decay `0.01` |
| LR schedule | cosine，warmup 3%，gradient clipping `1.0` |
| 精度 / 重计算 | BF16，gradient checkpointing / full uniform recompute |
| CPU staging | 开启；2 套 GPU 暂存槽，每种 FP32 张量每槽最多 128 MiB |
| Chunked loss | 开启，chunk size 1024，entropy 梯度关闭 |
| R3 / MTP | 关闭 |
| 镜像 | `/storage/openpsi/images/areal-dev-sglang-1014.sif`，沿用 RL actor |
| 保存 | HF 模型与 recovery checkpoint 均每 150 step；不额外按 epoch 保存 |
| 验证集 | 默认关闭，可在 data.yaml 单独指定，配合 evaluator 周期 |
| W&B | key 和 URL 均非空时启用 online，否则 disabled |
| reservation | 仅显式 `--reservation` 生效；省略使用公共池 |

`base.yaml` 固定模型、运行环境、并行与字段联动；`params.yaml` 放训练、优化器、
微批、主机资源与保存参数；`data.yaml` 只选择训练/验证文件。
数据相对路径相对于所选 `data.yaml`，与调用入口时的工作目录无关。
启动时不安装/升级依赖，也不会联网下载模型或数据。

## n_mbs 与长短样本

在设置 `max_tokens_per_mb` 后，`n_mbs` 表示每个 DP 分片的微批数下限。
`max_tokens_per_mb` 是一个微批在 CP 切分之前的 token 预算，并非整步的 token 上限。
若达到预算必须增加微批数，AReaL 会继续拆分，并使各 DP 分片的微批数保持一致。
每条样本作为整体分配；CP 在模型计算时切分序列。

默认不在 YAML 中设置 `n_mbs`，使用 AReaL 的默认下限 1，根据 token 预算自动拆分。
当前 global batch32 / DP2 / CP4：

| 32 条样本的长度 | 每个 DP 分片 | 每微批 CP 切分前 | 每个 CP rank 约处理 | 整步 |
| --- | --- | --- | --- | --- |
| 全部 256K | 16 条、16 个微批 | 1 条 × 256K | 64K token | 梯度累积后更新一次 |
| 全部 1K | 16 条、1 个微批 | 16 条共 16K | 4K token | 更新一次 |

短样本不会补到 256K。混合长度时按实际长度分配，并同步各 DP 分片的微批数。
如需调整最低微批数，可在 params.yaml 显式添加 `actor.mb_spec.n_mbs` 正整数；
省略字段即可恢复自适应默认行为，不应写成 null。
上述是分批行为说明，256K GPU 显存/吞吐仍需实际测试。

## CPU staging 的实际容量

这与按层卸载 activation 不同。AReaL 将 CPU 中的 FP32 master 参数及 Adam moments
按参数组展平，再切成固定容量的 update units；unit 可以跨多个小参数，也可以将大参数切开。
不要求装下一整层，CPU 中的完整 optimizer state 也不受 128 MiB 限制。

`buffer_count=2` 是两套可复用的 GPU 暂存槽。每套包含 master、grad、exp_avg、exp_avg_sq
四个 FP32 张量，因此 `2 × 128 MiB` 这个说法不完整：每个 optimizer 实例的持久暂存张量
最多为 `2 × 4 × 128 MiB = 1 GiB`。MoE 的 dense/expert optimizer 实例各自有槽；
两个实例各达到容量上限时，每 rank 的这部分暂存约 2 GiB，另有计算临时开销。
RL 也使用 `buffer_count=2 / bucket_size_mb=128`，这是 AReaL 的默认值。

暂时保留 128 MiB；增大 bucket 会减少分块次数，但增加显存占用，不能仅凭模型大小断定更快。
例如改为 256 MiB 时，每 optimizer 实例的上述暂存上限变为 2 GiB。

## 多轮纯文本 JSONL

使用 AReaL 的 `get_swe_sft_dataset` 与 `SFTTrainer`。直接调用数据加载函数，
不依赖文件名中必须包含 swe。`swe.split_mode=trajectory` 将一整段对话作为一条样本，
所有有效 assistant turn 参与 loss；user/system/tool 文本作为上下文。
保留已有 thinking，开启原生 Qwen3.5 模板需要的 tool-call 参数解析。
超过 max_length 的样本由 AReaL 过滤，不静默截断。

本地模型使用较早版本的原生 Qwen3.5 chat template。AReaL 的
`areal/dataset/swe_sft/tokenization.py` 已补充该版本的显式匹配与多轮 loss-mask 测试；
若切换 AReaL checkout，需确保包含此改动。补丁仅修改内存中的训练模板，不修改模型文件。

每行一个对象，例如：

```json
{"messages":[{"role":"user","content":"计算 2+3。"},{"role":"assistant","content":"5。"},{"role":"user","content":"再乘以 4 呢？"},{"role":"assistant","content":"20。"}]}
```

本 profile 要求文本 content；不会静默删除 image/audio/video payload。
预处理保留每 trial 的 Arrow 缓存，并导出少量 text/token/loss-mask 样本供检查。

### 示例数据

采用共享目录已有的 [ShareGPT/Vicuna 数据集](https://huggingface.co/datasets/anon8231489123/ShareGPT_Vicuna_unfiltered)，
源文件为 `/storage/openpsi/data/ShareGPT_Vicuna_unfiltered/ShareGPT_V3_unfiltered_cleaned_split.json`。
上游声明 Apache-2.0。`prepare_example.py` 流式读取 JSON 数组，转换 human/gpt 为
user/assistant，只选至少两轮的完整对话，避免同一原始对话的切片跨训练/验证集合。
默认生成 64 条训练对话和 16 条验证对话；这是启动样例，不代表完整训练数据。

首次准备（已经存在则跳过，脚本拒绝覆盖）：

```bash
python3 prepare_example.py
```

生成的 `data/sharegpt-train.jsonl`、`data/sharegpt-valid.jsonl` 和 `data/source.json`
已 gitignore。source.json 记录来源、完整源文件 SHA256、子集 SHA256 和样本 ID。
默认 data.yaml 使用训练子集；验证文件已准备，但不自动开启评估。
可以通过 `--source` 指定另一份相同结构的本地 ShareGPT JSON 文件，全程无需联网。

## 使用

从本目录执行，提交端只需 Python 3.10+ 与 PyYAML：

```bash
./submit.sh --dry-run --experiment-name qwen35-sft --trial-name inspect
./submit.sh --check-only --experiment-name qwen35-sft --trial-name preflight01 --reservation rsi
./submit.sh --experiment-name qwen35-sft --trial-name trial01 --reservation rsi
```

`--dry-run` 只显示合并配置及 sbatch 命令，不分配资源或创建实验目录。
`--check-only` 临时申请 8 CPU、64 GiB、0 GPU，使用真实训练镜像检查原生配置加载、
tokenizer、JSONL、tokenization 与 loss mask，并确认过滤后至少有一个完整 global batch。
提交命令会等待预检结束；成功删除 `.checks/` 临时目录，失败保留日志，且不占用正式 trial。
这是可选 CPU 预检作业，正式 SFT 仍只有一个 GPU 作业，无常驻 controller。

正式运行目录：`/storage/openpsi/experiments/logs/<user>/<experiment>/<trial>/`，
包含三份配置快照、train.yaml、启动脚本、manifest、Slurm job id 与 `train-<jobid>.log`。
checkpoint 位于同一 fileroot 的 `checkpoints/<user>/<experiment>/<trial>/`。
短样例只有 2 step，默认 150-step 周期不会产生 checkpoint；如测试保存，可用参数副本修改周期。
已有正式 trial 不被覆盖，需要新 trial 名称。取消训练只需 `scancel <jobid>`。

可用 `--params`、`--data`、`--fileroot`、`--partition` 和 `--credentials-file`。
AReaL 源码路径优先读取非空的环境变量 `AREAL_DIR`；未设置时，从按下述规则发现的
`credentials.env` 中解析 `AREAL_DIR`。base.yaml 不再保存源码路径，也不提供 `--areal-dir`。
环境变量中的相对路径以提交命令的工作目录为基准；文件中的相对路径以凭据文件所在目录为基准，
两者均支持绝对路径和 `~`。解析后的路径用于 PYTHONPATH、Slurm chdir 和源码校验，
并固定传给容器；环境变量优先级在容器内保持一致。

例如在 `credentials.env` 中添加（或在提交 shell 中 export）：

```bash
export AREAL_DIR='/absolute/path/to/AReaL'
```

提交端仅解析该路径赋值，支持 `export`、引号及行尾注释，不执行凭据文件中的 shell 命令，
也不展开 `$VAR`、`$(...)` 等 shell 表达式。两处都未设置时，在提交 Slurm 前报错。

凭据优先级为 CLI > CREDENTIALS_FILE > sft/credentials.env > ../credentials.env；
缺少凭据时可离线训练，W&B 关闭。模板为 `credentials.env.example`，真实文件已忽略。
除提交端解析 AREAL_DIR 外，训练凭据在容器内加载；key/token 不会放进命令行、manifest 或快照。

## 检查

```bash
python3 -m pytest ../rl/test_submit.py test_submit.py --import-mode=importlib
bash -n submit.sh train.sbatch worker.sh
```

离线检查覆盖单作业资源命令、公共池规则、路径解析、临时预检清理、文本边界及 W&B 自动启用。
CPU 数据预检通过仍不代表 256K GPU 前后向、optimizer step 或恢复已经验证。

本地验证：RL/SFT 入口测试 56 项、AReaL SFT 数据集与缓存测试 64 项通过。
真实 actor 镜像的 Slurm CPU 预检（989848）通过，64 条训练对话全部保留，长度 152–2459 token；
成功后预检临时目录已删除。随后 GPU 试跑 989858 使用默认 ShareGPT 小样例完成 2 step，
Slurm 状态为 COMPLETED / 0:0，loss/avg 约 0.663、0.515，两个 step 的 update_successful 均为 1。
本次使用短对话样例，尚未验证满 256K 输入、150-step 保存或恢复。
