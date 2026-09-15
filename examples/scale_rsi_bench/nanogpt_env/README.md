# Scale RSI Bench nanoGPT：Slurm / Singularity 环境

脚本、源码、虚拟环境、数据和日志统一存放在 `nanogpt_env/`。
以下启动命令均从父目录 `rsi/scale_rsi_bench/` 执行；文中数据、缓存和日志相对路径以 `nanogpt_env/` 为根。

任务：[scaleapi/rsi-benchmark 的 nano-gpt-data-curation](https://github.com/scaleapi/rsi-benchmark/tree/main/tasks/nano-gpt-data-curation)。
源码固定在 `3061df5c8fa466830fca8c82fe2b7a1f14708b62`，位于 `source/`。
镜像默认 `/storage/openpsi/images/areal-dev-sglang-0821.sif`。

这是官方任务训练、数据准备和评估脚本的 Slurm 适配环境；没有配置 Harbor/Modal 或自动 agent 运行。
官方任务要求 1 张 H100；本集群已探测到 L20X，时间预算和吞吐不直接等同于 H100。

2026-09-10 已完成准备：六项固定依赖安装成功，41 项依赖一致性检查通过；
L20X 上的两步原版训练 smoke test 通过；182,016 篇文档、200,094,380 tokens
的数据池已重建，官方 `tests/SHA256SUMS` 中的三项资产全部校验通过。
2026-09-11 已完成数据筛选优化：14 次完整 dev + 1 次最终正式评估，
最佳 dev PPL **303.123**，正式隐藏集 PPL **296.22**（官方随机基线 425.59，降低 30.40%）。
最终提交及从头复现检查均已完成，详见 [实验报告](research/RESULTS.md)。

## 使用

推荐 agent 使用新的异步实验入口，提交指定的文档 ID 列表：

```bash
bash nanogpt_env/bench.sh /absolute/path/selection.json
```

每次实验独立保存输入、日志、状态和结果，申请 2 小时作业上限并运行固定的 3000 步训练。
详细接口与短测试方法见 [AGENT_QUICKSTART.md](AGENT_QUICKSTART.md)。以下为原有交互入口。

在项目根目录执行：

```bash
cd /storage/openpsi/users/wanghaitao.wht/project/rsi/scale_rsi_bench
bash nanogpt_env/scripts/slurm.sh
```

默认申请 1 GPU、16 CPU、64 GB 内存、2 小时，进入已激活 `.venv` 的容器交互终端。
入口显式保留 Slurm 的 `CUDA_VISIBLE_DEVICES`，避免 `--cleanenv` 后访问到其他作业的 GPU。
宿主机 `workspace/` 映射为容器 `/workspace`，虚拟环境和缓存保存在共享存储，退出容器后仍然有效。
`/tmp` 使用容器独立临时目录；需要保留的结果请写入 `/workspace` 或共享存储。

可以覆盖资源：

```bash
RSI_GPUS=1 RSI_CPUS=16 RSI_MEM=64G RSI_TIME=04:00:00 bash nanogpt_env/scripts/slurm.sh
```

在容器中阅读 `/workspace/instruction.md`，编写 `submission/curate.py` 并生成
`submission/selection.json`（按优先级排列的文档 ID，覆盖至少 12M tokens）。
正式提交还要求 `claim.md`。准备环境不会自动生成候选方案。

开发集完整评估（官方 3000 步训练）：

```bash
# 从项目根目录提交，日志位于 nanogpt_env/logs/dev-<jobid>.log
sbatch nanogpt_env/scripts/dev.sbatch
# 或在已经进入的容器终端内：
bash /workspace/dev_eval.sh
```

最终 held-out 评估（从宿主机项目目录执行）：

```bash
bash nanogpt_env/scripts/slurm.sh verify
```

每次 verifier 使用独立的 `results/verifier-*/`，输出在其中的 `logs/verifier/`。
检查 `result.json` 的 `status` 和 `score`；官方 verifier 对无效提交也可能返回 shell 退出码 0。
开发迭代请使用 dev 集，held-out 集仅用于最终评估。

## 重建和检查

```bash
# 完整准备，可重复执行；宿主机 python 需要 huggingface_hub（当前已有）
bash nanogpt_env/scripts/prepare.sh

# 独立 GPU / tokenizer / 原版训练脚本 smoke test
bash nanogpt_env/scripts/slurm.sh python /storage/openpsi/users/wanghaitao.wht/project/rsi/scale_rsi_bench/nanogpt_env/scripts/smoke.py
```

`nanogpt_env/requirements-nanogpt.txt` 固定官方 Dockerfile 的六个直接依赖，包括 PyTorch 2.8.0。
`.venv` 使用镜像内 Python 3.12，须在同一镜像中运行。
镜像预设代理不可用，容器入口会清除代理配置；依赖通过阿里云 PyPI 镜像下载。
数据准备默认通过 `hf-mirror.com` 获取官方固定 revision，公开文件下载不会发送 Hugging Face token。
可以通过 `HF_ENDPOINT` 改变数据下载站点。

`nanogpt_env/scripts/build_data.py` 调用官方 `build_pool.py` 重建 182,016 篇文档、200,094,380 tokens，
必须通过官方 token 数组 SHA256 校验：
`01b71c620642b8e8318d665d8de6a9988eaec1596586e8d7ee63602d605bc187`。
不通过校验不会视为准备成功。原始下载缓存、token 数组和 JSONL 占用数 GB；Python/CUDA 依赖也需要数 GB。

整理后已删除 Python 依赖下载缓存和 FineWeb/C4 原始下载缓存，保留离线必需的 GPT-2 tokenizer。
现有数据池可直接使用；`prepare.sh` 发现完整数据池时会跳过原始语料下载。
如需重新构建缺失的数据池，会重新下载原始语料。
`pool/pool.jsonl` 与 `workspace/data/pool.jsonl` 是硬链接，共享同一份磁盘数据，请勿编辑任务输入。

运行时默认 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`。
这只是库的离线模式，不是网络或评测安全隔离；`/storage` 的全目录绑定也不隔离测试资产。
如需公平的自动 agent 评测，应另行配置权限、网络隔离和计时。
官方 `timer.sh` 已保留，但环境安装和交互终端不会自动开始消耗任务预算。

`logs/smoke-result.json` 是两步合成数据训练的环境检查结果，不是 benchmark 成绩。
完整安装版本清单位于 `logs/installed-requirements.txt`。
