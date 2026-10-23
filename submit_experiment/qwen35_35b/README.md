# Qwen3.5-35B Slurm 实验提交

本目录独立包含 Qwen3.5-35B-A3B 的脚本、文档、配置和离线测试。
其他模型在 `submit_experiment/` 下另建目录，维护各自的入口和配置。
Slurm 申请一个 CPU controller，AReaL 的
Slurm scheduler 再申请一个 8 GPU 节点。actor 使用 8 个 worker，rollout 使用
1 个 TP8 worker，绑定 actor 所在节点并共用 GPU；proxy 在 rollout 侧运行。
不使用 local scheduler，不改 AReaL launcher/scheduler。

## 默认配置

| 项目 | 默认值 |
| --- | --- |
| 模型 | `/storage/openpsi/models/Qwen__Qwen3.5-35B-A3B` |
| 训练并行 | Attention DP2 × CP4，TP1、PP1；FFN EP8、ETP1、EDP1 |
| 推理并行 | SGLang DP1 × TP8 |
| 主机内存申请 | actor 每卡 128 GiB、整节点 1024 GiB；rollout 每卡 64 GiB、TP8 实例 512 GiB |
| 上下文 | 262144（256K）；请求总 token 上限 262143，预留一个 token |
| CPU-staged optimizer offload | 开启，2 个 buffer，每个 128 MiB |
| Chunked logits / loss | 开启，loss chunk size 1024 |
| SGLang CUDA graph | decode 开启、prefill 关闭，保留 memory saver；配置 max batch size 512 |
| 训练 batch / Rollout 队列 | batch size 64，`queue_size=128`；队列必须大于 batch size 以留出任务提交空间 |
| 采样 / 过滤 | 训练和评估 `n_samples=8`，最小有效组 8，使用示例的 `filter_function` 过滤同奖励组 |
| 并发 / 微批 | rollout 并发 64，SGLang 最大请求数 16；`n_mbs=128`、`n_mbs_divisor=8` |
| Arena 路由 | `gateway` |
| R3 / MTP | 关闭 |
| controller / actor 镜像 | `/storage/openpsi/images/areal-dev-sglang-1014.sif` |
| rollout 镜像 | `/storage/openpsi/images/deepep-base-119b5ffe.sif` |
| Slurm 分区 / reservation | `all` / 默认不指定 reservation |
| AReaL fileroot | `/storage/openpsi/experiments`，与所给实验一致 |
| W&B | 凭据文件中 key 和 URL 均非空时自动开启 online，否则默认关闭 |

三份配置按 `base → params → arena` 合并：

- `base.yaml` 固定模型、运行环境、依赖 overlay、调度和并行策略、字段联动。
- `params.yaml` 放采样、RL、优化器、batch、微批、并发、显存预算、超时、保存恢复、
  W&B 等可调参数。`launch.slurm` 也可以在这里覆盖，例如 CPU、内存和时限。
- `arena.yaml` 仅放 `econfig` 中的数据选择：Streams、Harness、reward 版本、混合权重、
  数据集级环境变量。凭据单独保管。

RL、过滤、优化器超参、微批、SGLang 显存比例（0.7）及保存恢复策略默认沿用参考实验
`main-r3-20261016/train.yaml`；batch、采样数、并发、队列和 graph 上限使用上表指定值。
其他差异包括约定的模型、单机并行、R3/MTP
关闭、主机内存、W&B 自动发现及 Qwen3.5 兼容设置：CPU staging 使用 precision-aware
optimizer，chunked loss 不计算 entropy 梯度，prefill graph 关闭，请求总 token 上限
比 256K 上下文少 1。评估采样配置也沿用示例，并保留同样的 1-token 上下文余量。

`cuda_graph_max_bs=512` 是配置上限；当前 `sglang.max_running_requests=16`，所用 SGLang
会按请求池大小截断实际 capture batch。rollout workflow 并发 64 与 SGLang 同时执行请求数
是两个独立参数。当前 Megatron 路径还会将 `n_mbs_divisor` 按 PP 大小重设：本模板保留配置
值 8，但 PP1 下该路径实际使用 1。

参数文件不能覆盖 base 的固定字段。字典深度合并；`scheduling_spec` 按现有单条列表项
合并，因此在 params 中改 CPU/主机内存不会丢失镜像和启动命令。其他列表整体替换。
合并后会展开 `ref.megatron`、`ref.mb_spec` 和 `ref.scheduling_spec` 的容器引用，使 ref
跟随 actor 的最终设置，并兼容 AReaL 的 OmegaConf 结构化配置加载；容器内部的环境变量引用保留。

`scheduling_spec.mem` 的单位为 GiB。rollout 会先按 `TP × PP` 放大单实例申请，再按
该节点的 DP 实例数汇总；训练侧的 CP 不参与 rollout 资源计算。默认 actor 与 rollout
在 GPU 节点上合计申请 1536 GiB，正好用满当前节点的 Slurm 内存额度。CPU controller
另需 64 GiB，应调度到其他节点；GPU 节点若已有其他作业占用内存，可能无法同时启动两者。

2026-10-23 已在 `rsi` reservation 实测资源分配：actor `985277`（32 CPU、1 TiB、8 GPU）
与 rollout `985278`（64 CPU、512 GiB、不重复申请 GPU）同时运行在 `slurmd-67`，
controller `985276`（4 CPU、64 GiB）位于另一节点；三个作业均 `COMPLETED / 0:0`。
该测试只验证资源能同时申请，不加载模型、不执行训练；测试资源和临时文件已清理。

2026-10-23，使用对齐后的默认配置实际启动 `qwen35-try/trial06`：controller `985296`、
actor `985297`、rollout `985300`；actor 和 rollout 同驻 `slurmd-67`。训练引擎、SGLang
和 proxy 均完成初始化；运行接口报告 decode graph=`full`、max batch=16、prefill
graph=`disabled`、memory saver 开启。调度器已提交 16 个并发 rollout，Arena 模型路由
注册成功并发起任务请求。该结果验证启动与任务提交，尚不代表首个训练 step 或完整训练通过。

随后以当前 batch64 / queue128 / 采样8 / rollout并发64 / graph上限512 配置启动
`qwen35-try/trial08`：controller `985305`、actor `985308`、rollout `985310`。本次显式允许
历史健康率异常，覆盖记录见该轮 `preflight.json`。actor、SGLang、proxy 均就绪，已提交
64 个并发 rollout 并发起 Arena 任务；接口确认 graph 配置上限 512，实际 capture 最大 16。
首个训练 step 及完整训练仍未验证。

## 使用

提交端需要 Python 3 和 PyYAML；不安装新依赖。训练依赖来自已有 SIF 和源码 overlay。
默认 AReaL 源码为本仓库旁边的 `../AReaL`，可用 `--areal-dir` 或 `AREAL_DIR` 指定。

```bash
cd /storage/openpsi/users/wanghaitao.wht/project/rsi/AReaL-RSI/submit_experiment/qwen35_35b

# 只输出合并配置和计划提交命令，不创建目录、不调用 Slurm / Arena、不读取凭据内容。
./submit.sh --dry-run --experiment-name qwen35-arena --trial-name inspect

# 私有文件由 controller 和 workers 加载，内容不会复制到提交目录。
cp credentials.env.example credentials.env
chmod 600 credentials.env
# 编辑 credentials.env 填入 Arena token，以及可选的 W&B key / URL。
# 入口自动查找脚本同目录的 credentials.env，无需 export。

# CPU 环境/API/Stream 预检；不申请 GPU workers，不启动 Arena rollout 任务。
# 使用临时目录，成功后自动删除，不占用 trial 名称。
./submit.sh --check-only --experiment-name qwen35-arena --trial-name preflight

# 预检 job 成功结束后再提交训练，默认 100 个训练 step。
./submit.sh --experiment-name qwen35-arena --trial-name exp01

# 需要 reservation 时，预检和训练命令都加此选项。
./submit.sh --experiment-name qwen35-arena --trial-name exp02 --reservation YOUR_RESERVATION

# 自定义参数、数据、源码、产物目录。
./submit.sh --experiment-name another-experiment --trial-name trial01 \
  --params /shared/params.yaml --arena /shared/arena.yaml \
  --areal-dir /shared/AReaL --fileroot /shared/experiments
```

reservation 只认显式的 `--reservation`，不指定就使用公共池；忽略环境变量和旧配置中的
reservation。controller、actor、rollout 同步使用所选 reservation。
默认没有固定 nodelist；rollout 的节点约束由 AReaL 根据 actor 作业确定。
controller 与 GPU workers 是独立 Slurm job，“单机”指一个 GPU 节点。
不要将 actor 设置为 `exclusive: true`，共卡 rollout 还需要同节点 CPU/内存资源。

停止旧轮次后，旧 sandbox 任务可能因推理服务结束而失败，使 stream 最近的健康率检查
拦住重启。核实历史失败原因后，可在本次使用的参数副本中设置
`launch.preflight_allow_unhealthy: true`；默认是 `false`。这只允许历史终态任务数量/失败率
异常，Stream、Harness、reward 和数据可用性校验仍然执行。覆盖状态写入 `preflight.json`
的 `unhealthy_override` 并打印到 controller 日志，不会修改 Arena 的任务记录。

`--experiment-name` 是实验组名，默认使用 `base.yaml` 的 `experiment_name`（`qwen35-35b-arena`）；
`--trial-name` 是组内单次运行的名称，默认 `trial-YYYYMMDD-HHMMSS`。两者分别写入
AReaL 的 `experiment_name`、`trial_name`，并用于 Slurm controller 名称和产物目录。
同一 experiment 可以有多个 trial，不同 experiment 也可使用相同的 trial 名称。
`--check-only` 和 `--dry-run` 均可使用与正式训练相同的 experiment / trial 名称，也可重复执行。

`credentials.env.example` 是可提交的凭据模板，真实的 `credentials.env` 已加入本目录
`.gitignore`。默认查找脚本同目录的 `credentials.env`，从其他工作目录调用入口也能找到。
路径优先级为 `--credentials-file` → 非空的 `CREDENTIALS_FILE` 环境变量 → 默认文件；
显式提供的相对路径按提交时的工作目录解析，最终保存绝对路径供 Slurm 作业使用。
需要使用其他共享路径时，例如：

```bash
export CREDENTIALS_FILE=/absolute/shared/path/to/credentials.env
# 或者 ./submit.sh --credentials-file /absolute/shared/path/to/credentials.env ...
```

文件中设置当前 Arena 部署的认证信息，并可同时设置 W&B：

```bash
export ARENA_OPENAPI_BASE=https://arenaonline-prod.alipay.com
export ARENA_OPENAPI_TOKEN='<private token>'
# 可选：仅在 gateway 使用独立 key 时设置。
# export ARENA_LLM_API_KEY='<gateway key>'
# 可选：同时提供下面两项，自动开启 W&B online。
export WANDB_API_KEY='<private W&B key>'
export WANDB_BASE_URL='https://your-wandb-server'
```

| 凭据字段 | 用途与填写方式 |
| --- | --- |
| `ARENA_OPENAPI_BASE` | Arena OpenAPI 服务地址，模板已填写所用部署地址 |
| `ARENA_OPENAPI_TOKEN` | 必填，自己的 Arena OpenAPI token 原始值，不加 `Bearer ` 前缀 |
| `ARENA_LLM_API_KEY` | 可选，gateway 的独立 key 原始值，不加 `Bearer ` 前缀；省略时复用 OpenAPI token |
| `WANDB_API_KEY` | 可选，自己的 W&B API key；与 `WANDB_BASE_URL` 一起填写后自动启用 |
| `WANDB_BASE_URL` | W&B 服务地址，自建服务填写自己的地址；使用官方服务时填写 `https://api.wandb.ai` |

本部署如使用同一 bearer，可以省略 `ARENA_LLM_API_KEY`，入口会在内存中使用 OpenAPI
token。可选设置 `SWE_RL_ADMIN_API_KEY`，否则每次提交在 controller 中随机生成。
不要把其他人的凭据路径或 token 复制到 YAML。

controller 加载凭据后，若 `WANDB_API_KEY` 和 `WANDB_BASE_URL` 都非空，就设置
`WANDB_MODE=online`；缺少任一项则设置为 `disabled`。默认 params 通过环境变量选择模式，
project 使用 experiment name，run name 使用 `experiment_name-trial_name`。
需要手动选择 `offline` 等模式时，可以显式修改 params 中的 `stats_logger.wandb.mode`。
W&B key 不写入提交产物，URL 和 key 直接通过运行时环境传给 W&B。

首次试跑可复制参数文件，将训练缩短到 3 步，保留 256K 上下文和默认并行策略：

```bash
cp params.yaml params.try.yaml
python3 - <<'PY'
from pathlib import Path
import yaml

path = Path("params.try.yaml")
params = yaml.safe_load(path.read_text())
params["total_train_steps"] = 3
params["actor"]["optimizer"]["warmup_steps"] = 1
path.write_text(yaml.safe_dump(params, sort_keys=False))
PY
./submit.sh --params params.try.yaml --experiment-name qwen35-try --trial-name trial01
```

调整采样、RL、优化器或资源参数编辑 `params.yaml`（或通过 `--params` 选择自己的副本）；
换数据编辑 `arena.yaml`（或使用 `--arena`）。默认 stream 为 `swemm-rl64-20260922`，
harness 为 `areal-harness@0.2.0-oss-rc.6-swe-mm-256k`。

## 预检、产物和运行边界

默认 `cluster.fileroot=/storage/openpsi/experiments`。AReaL 框架自身的默认根目录是
`/tmp/areal/`，这里沿用所给实验的共享存储配置。提交记录直接放入 AReaL 原生实验日志目录。

| 路径（相对 fileroot） | 内容 |
| --- | --- |
| `logs/<user>/<experiment>/<trial>/` | 提交配置、启动脚本、controller 作业信息和 AReaL 训练日志 |
| `checkpoints/<user>/<experiment>/<trial>/` | AReaL 保存的模型、优化器和 recovery checkpoint |
| `logs/<user>/.checks/<唯一目录>/` | CPU 预检临时数据，成功后自动删除，失败或取消时保留 |

用户名采用 AReaL 的 `getpass.getuser()` 规则，可能受 `LOGNAME` / `USER` 影响，
与 shell 提示符或 `id -un` 不一定相同；以提交入口输出的目录为准。
`--fileroot` 同时改变日志和 checkpoint 的共享根目录。

正式训练目录包含：

- `base.yaml`、`params.yaml`、`arena.yaml` 和合并后的 `train.yaml`。
- `controller.py`、`controller.sbatch`、`environment.sh`、`controller.jobid` 和 `controller-<jobid>.log`。
- `manifest.json`（源码 HEAD/status、配置 hash）、`source.patch` 和运行时版本记录。
- AReaL 原生生成的 `config.yaml`、`main.log`、`actor.log`、`rollout.log`、`merged.log`，
  以及按实际启用功能生成的轨迹、W&B 本地记录等；Arena 结果保存在 `arena_results/`。

正式训练目录权限 0700；同一实验和 trial 的目录不覆盖。配置中的凭据保持
环境变量引用。`train.yaml` 仍含 OmegaConf 插值，由 controller 使用 AReaL 原生
`SWEPPOConfig` 解析，校验与训练读取同一份文件。

`--dry-run` 仅离线合并/校验配置并输出到终端，不创建目录或文件，不占用 trial 名称。
`--check-only` 提交 CPU Slurm 预检作业，使用 `<fileroot>/logs/<user>/.checks/` 下的唯一临时目录。
预检配置的 fileroot 也临时指向该目录，AReaL 在加载配置时产生的文件同样受清理覆盖。
入口使用 `sbatch --wait` 等待 CPU 预检结束，再由提交端删除成功预检的整个临时目录，
避免在作业内部删除仍被 Slurm 占用的 NFS 日志。命令会等待并直接输出最终结果；
失败或取消时保留该目录用于排查，正式 trial 目录始终不会被创建或覆盖。
两种检查模式互斥。预检结束后可直接用相同的 experiment / trial 名称提交训练。

实际 CPU controller 会在提交 GPU workers 前检查：
实际加载的 AReaL 路径、两种镜像的运行时、AWEX Qwen3.5 converter、支持的 SGLang 版本、
CP4 所需 Megatron Core ≥0.18.2 / Megatron Bridge ≥0.5.1，以及 Arena Stream/Harness/reward。
复用原实验的 SIF、AWEX/Transformers/proxy overlays 和 GDN 只读补丁；不重建镜像。
这些路径位于 base，可以在另一个环境中显式替换。

当前 rollout 镜像的 breakable prefill CUDA graph 不支持 `enable_memory_saver=true`，
会在启动推理服务时报 `Breakable CUDA graph is not compatible with memory saver mode`。
默认设置 `sglang.disable_cuda_graph: false` 和 `sglang.disable_prefill_cuda_graph: true`，
只关闭 prefill graph，保留 decode CUDA graph 和 AWEX memory saver。当前 rollout 镜像
已确认支持 `--disable-prefill-cuda-graph`；所选 AReaL 源码还需包含对应的可选配置字段，
旧版源码会将该字段视为未知配置。该字段与多模 chunked loss 准入修复见
[AReaL PR #1821](https://github.com/areal-project/AReaL/pull/1821)。参考实验使用 `disable_cuda_graph: false`、
`cuda_graph_max_bs: 16` 和 `enable_memory_saver: true`；本模板保留开启状态，graph 上限改为 512。
同一镜像的模型兼容规则会为未进入 breakable 白名单的多模模型自动关闭 prefill graph；
Qwen4-Exp 被明确排除，而 Qwen3.5 MoE 在白名单中。因此参考实验中的全局 graph 开关开启，
不代表它启用了 breakable prefill；换到 Qwen3.5 后需要显式关闭这一阶段。
将来启用 prefill graph 前，应确认所选 backend 与 memory saver 兼容。

先前的 `trial06` 已验证 256K、CP4、双镜像的服务初始化与实际推理；完整 RL 训练、峰值、
收敛、权重同步和恢复仍需验证。更改容量参数后也应重新确认启动与采样行为。

默认 `arena.yaml` 保留所给实验的 SWE-MM 数据选择。Qwen3.5 的 Megatron-Bridge
`MODEL_THD` 路径在视觉编码、embedding 融合和 mRoPE/CP 切分后返回 hidden states，再执行
chunked LM Head；不需要因带图输入关闭 chunked loss。所选 AReaL 源码需保留本次修正的
Qwen3.5 多模准入检查（旧版本会在前向前误拒绝）。这不代表任意 VLM/padded 路径均支持。

改变 optimizer 类型时注意 CPU staging 当前要求 `adam`；开启 entropy 梯度需关闭
chunked loss；staging 不支持异步 checkpoint。`kl_ctl > 0` 时已有 ref 配置会按需创建
额外模型，内存预算需要重新确认。改变上下文时同时调整 `gconfig.max_tokens`、
`sglang.context_length` 和 `actor.mb_spec.max_tokens_per_mb`，提交端会检查这些约束。

通过日志目录中的 `controller.jobid` 和 Slurm 查看 controller；actor/rollout 的作业记录和日志
也由 AReaL 写入该目录，checkpoint 则位于上表列出的独立目录。取消 controller 会转发 TERM，让 trainer 清理自己创建的
worker；强制 SIGKILL、节点故障或网络中断后仍需核对残留 Slurm jobs 和 Arena tasks。
此入口不重启原实验，不自动重试提交，也不自动取消其他作业或平台任务。
恢复相关参数在 params 中；需要保留原 trial、fileroot 和完整 recovery checkpoint，
新建一个 experiment 或 trial 名称不是恢复旧实验。

例如查看上面 3 步试跑的状态和 controller 日志：

```bash
log_user=$(python3 -c 'import getpass; print(getpass.getuser())')
run=/storage/openpsi/experiments/logs/$log_user/qwen35-try/trial01
job=$(cut -d';' -f1 "$run/controller.jobid")
squeue -u "$USER"
sacct -j "$job" --format=JobID,JobName,State,ExitCode
tail -f "$run/controller-$job.log"
# 需要停止此次运行时：scancel "$job"
```

`--check-only` 会等待预检结束：退出码 0 并输出 `CPU runtime and Arena preflights PASSED`
表示成功；失败时命令以非零码退出并提示日志目录。等待期间也可用 Slurm 输出的 job ID
查询状态，主作业 `COMPLETED` 且 `ExitCode=0:0` 表示成功。
运行期间可以查看输出的临时目录中的 `controller-<jobid>.log`，失败时该日志会保留；
成功后临时目录和其中的 `preflight.json` 都会被删除。

```bash
job=替换为提交预检时输出的job编号
sacct -j "$job" --format=JobID,JobName,State,ExitCode
```

controller 日志还会输出 `W&B automatic mode: online` 或 `disabled`，用于确认凭据自动检测结果。
重复预检无需改 trial 名称；正式训练的已有 trial 目录仍不会被覆盖，重新提交训练需要换名。
清理由等待作业的提交进程执行；若提前关闭终端或终止该进程，临时目录可能保留，
作业状态仍可通过 Slurm 查询。之前版本已提交的预检作业仍运行各自保存的脚本。

## 离线检查

```bash
# 在本模型目录执行。
python3 -m pytest test_submit.py
bash -n submit.sh controller.sbatch
```
