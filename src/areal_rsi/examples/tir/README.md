# Slurm TIR RSI 示例

此目录放数学任务的 profile、提示词、Arena 注册与评分以及训练 recipe。共享代际流程在 `infra/coordination/generation.py`，AReaL 的 trainer 和 worker 生命周期在 `scripts/train.py`。所有入口只支持 Slurm，actor 与 rollout 使用 v2。

## Arena 执行与 Slurm 训练

安装生产 Arena CLI 并配置 profile 后运行 `arena doctor`。凭证使用 CLI profile 或受限权限的共享文件；文件向控制器与 worker 导出 `ARENA_OPENAPI_TOKEN`、`ARENA_LLM_API_KEY`、`AREAL_ADMIN_API_KEY`。容器内 CLI 可通过 `ARENA_CLI=/shared/bin/arena` 指定，避免依赖宿主机的 PATH。

初始 Harness 固定为 `claude-code-with-skills@5.0.2`。模型服务先在 Slurm 上启动，再注册 LLM Gateway 路由和版本化 Model；沙箱调用网关，训练 worker 保留在本地 Slurm 网络。控制 API 为 `https://htnn-gateway.alipay.com/arenaonlinegateway/openapi/v1`；本次实际验证的沙箱推理入口为 `https://arenaonline-prod.alipay.com/api`。

```bash
python -m areal_rsi.infra.coordination.arena sync-model \
  --source /shared/models/Qwen2.5-Math-1.5B --key tir-qwen25-math \
  --route /shared/run/route.json --output /shared/run/model-pin.json

python -m areal_rsi.examples.tir.cycle arena register \
  --output /shared/run/probe --model-pin /shared/run/model-pin.json \
  --run-id tir-synthetic-probe-UNIQUE --seed 20261002 --count 1024

python -m areal_rsi.examples.tir.cycle arena launch \
  --resources /shared/run/probe/resources.json --samples 2 --concurrency 4 \
  --receipt /shared/run/probe/job.json
```

`route.json` 是规范 JSON，包含 `endpoints`、`enabled` 与可选 `metadata`；endpoint 指明已启动服务的 `base_url`、`upstream_model`、`endpoint_id`、`inbound_protos: ["chat", "messages"]` 和探活配置。同步入口根据模型目录摘要生成独立网关别名与 Model 版本，不复用另一份权重的别名。输出 pin 包含 key、version、content_hash；模型路径不作为沙箱可访问路径。

每道合成题发布一个 Case，关联可复用 files Env；Case 自己挂载收集脚本所需的冻结源码。oracle 只进入独立 Reward 沙箱，支持 Arena 的目录挂载。初始冒烟奖励严格比较最后一个 boxed 数字的精确有理数值；完整复现用 `register-math-reward` 切换到未改变的历史 Claude boxed 评分函数（内部使用 `strict_math_reward`），同时冻结 math-verify、SymPy 和解析器依赖。缺失或歧义 oracle、无成功终态、网关和评分器异常均作为执行失败保留。两个评分协议应分别标注。

```bash
python -m areal_rsi.examples.tir.cycle arena register-math-reward \
  --resources /shared/run/probe/resources.json --output /shared/run/probe-math
```

后续 launch 和 register-training 使用 `probe-math/resources.json`。独立评测集可向 `register` 传入 `--exclude-task-ids`，引用规范 JSON 题目 ID 列表，确保排除所有校准/训练题，同时保持八个题族的相同配额。

```bash
python -m areal_rsi.examples.tir.cycle arena register-training \
  --resources /shared/run/selected/resources.json \
  --output /shared/run/train --run-id tir-synthetic-train-UNIQUE

python -m areal_rsi.scripts.train \
  --recipe areal_rsi.examples.tir.train_arena.prepare --no-training-eval \
  --training-resources /shared/run/train/training-resources.json \
  --config /shared/run/train/config.yaml --validate-only
```

训练集先从完成的两次采样 Job 中选择，排除执行失败和两次均答对的题，复用历史选题策略，优先一次答对的题并保留题族覆盖：

```bash
arena job get ACTUAL_PROBE_JOB --json > /shared/run/probe-result.json
python -m areal_rsi.examples.tir.cycle arena select \
  --resources /shared/run/probe-math/resources.json \
  --job-report /shared/run/probe-result.json --seed 20261002 --count 512 \
  --run-id tir-calibrated-train-UNIQUE --output /shared/run/selected
```

后续训练注册使用 `selected/resources.json`。选题输入、完整 Job 结果、摘要和题目引用均被冻结；模型或 Harness 引用不匹配时拒绝选题。独立评测集可用 `bind --reward-pin ...` 绑定同一评分器，发布新的 Benchmark 版本。

生产 Stream 目前按 Env 圈题，因此训练注册把 Case 数据投影为版本化 legacy Env；评测仍使用 Case/Benchmark。训练 recipe 复用真实 AReaL `ArenaStreamAgentWorkflow`：每次采样注册当前 v2 代理会话、在 Arena 启动 Harness、等待独立奖励，然后导出生成 token/logprobs 给 Slurm GRPO。系统失败轨迹由 AReaL 拒收。

实际训练通过任务无关的 `scripts/submit_train.sh` 提交 CPU 控制器；GPU worker 由 AReaL 创建。配置必须使用 Slurm/v2、`rollout.agent.mode: inline`、`rollout.dump_to_file: true`，推荐 `export_style: concat`。Slurm 布局需要满足整节点分配；`SchedulingSpec` 的 `container_type: apptainer`、`image`、`mount` 决定 worker 容器。用 `additional_bash_cmds` 在 worker 内加载共享凭证文件，不将 token 写进 YAML、命令参数或日志。覆盖 worker PATH 时须保留镜像 Python 环境。

本次使用的 AReaL checkout 需要 [通用协议补丁](../../infra/runtime/areal-arena-protocol.patch)：在隔离副本根目录执行 `patch -p1 < /path/to/areal-arena-protocol.patch`，然后冻结修改后的完整源码并供控制器与 worker 使用。Anthropic 训练代理向 Arena 声明原生 Messages 接口，让网关直接转发请求；代理随后将 system 文本块转成纯文本，避免 LiteLLM 转成列表字符串。补丁也让 v2 执行器调用既有 Arena 产物与指标钩子，并传递 session ID。失败任务仍然拒收。可在训练镜像内配置 `AREAL_CHECKOUT` 和 `PYTHONPATH`，运行 `tests/test_areal_arena_protocol.py` 验证接口声明、真实协议转换、成功产物和失败拒收。

Harness 更新调用通用 `sync-harness --source ... --spec ... --key ... --output ...`；source 为可执行文件或包含 `claude`/`runner` 的目录。TIR 的 `arena_claude.py` 可与 Claude 二进制 `claude-real` 一起打包为数学 Harness，通过替换 system prompt 缩小上下文。它是新的 Harness 版本，不能标记为初始 5.0.2。注册时用 `--harness-pin` 选择新 pin。对相同题集使用 `arena bind --resources ... --model-pin ... --harness-pin ... --run-id ... --output ...` 冻结新引用，无需改写原题库。训练 Stream 自动更新默认 Harness/Reward，训练清单与 Job 都显式冻结新引用。

相同目录只允许重用相同源码、题集、模型和 Harness；发生变化必须使用新目录。Job 先写 `*.intent.json`，成功后写收据；响应丢失时拒绝重复提交。核对服务端 Job 后传入同一命令的 `--adopt-job ACTUAL_JOB_ID`，身份不匹配时拒绝认领。

在控制器与 GPU worker 的同一共享路径安装 `areal_rsi` 和 AReaL checkout。worker 的 `PYTHONPATH` 须包括 AReaL checkout 和本仓库的 `src`，以同时导入 `areal`、AReaL 的 `examples.tir` 和完整 RSI 包；不再需要仓库顶层 `examples`。站点配置负责镜像、Slurm/munge 挂载、分区、reservation 与 GPU 数。

```bash
python -m areal_rsi.examples.tir.prepare_torl \
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

Claude 数学求解、合成数据校准和连续实验实现位于本 example；部署脚本位于 `launch/`。其站点路径与历史实验预算仍需按实验检查。训练统一通过任务无关的 `scripts/train.py --recipe ...`；历史记录保持原位置，旧评分源码的校验支持读取原文件布局。
