# Agent Swarm Optimization

独立环境，复用镜像 `/storage/openpsi/images/areal-dev-sglang-0821.sif`。
默认资源：1 GPU、16 CPU、64G、8 小时；通过 `RSI_GPUS/RSI_CPUS/RSI_MEM/RSI_TIME` 覆盖。

从 `scale_rsi_bench/` 目录启动：

```bash
bash swarm_env/run.sh                 # srun 分配资源，进入 /workspace
bash swarm_env/run.sh smoke           # 单卡短测试，无正式训练/裁判费用
bash swarm_env/run.sh audit           # wheel RECORD 文件完整性检查
bash swarm_env/run.sh python --version # 在分配的环境中执行命令
```

运行 graph 时推荐使用独占服务进程、动态端口的入口：

```bash
RSI_EXCLUSIVE=1 bash swarm_env/run.sh serve python /workspace/selfcheck.py
```

它先启动本作业的本地 vLLM 服务，再执行命令，结束后关闭该服务。
`RSI_EXCLUSIVE=1` 申请独占节点，避免同节点遗留进程占用显存；会占用更多集群资源。
官方 verifier 仍固定使用 8000 端口；运行官方 verifier 前需确保该端口未被其他作业占用。

目录：`source` 固定版本 benchmark；`workspace` 实验代码和输出；`.venv`
独立 Python 依赖；`models` 模型链接；`cache/huggingface` 必需的模型/数据缓存；
`assets` 评测数据；`logs` 安装和验证记录。不要删除 Hugging Face 缓存，模型链接依赖它。

环境使用镜像的 Python 3.12，不与镜像内的 torch/vLLM 混用。
这是 Slurm 开发环境，不是隔离的官方 sandbox：绑定了 /storage，且本集群 GPU
不一定与官方 H100 相同。任务约束见 `workspace` 内的官方说明。

只有 `logs/dependency-integrity.json` 和 `logs/SMOKE_PASS.json` 均显示通过，
才表示完成本地环境验证；它们不是官方 benchmark 分数。
