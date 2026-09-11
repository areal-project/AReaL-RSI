# On-Policy Self-Distillation

独立环境，复用镜像 `/storage/openpsi/images/areal-dev-sglang-0821.sif`。
默认资源：4 GPU、32 CPU、128G、6 小时；通过 `RSI_GPUS/RSI_CPUS/RSI_MEM/RSI_TIME` 覆盖。

从 `scale_rsi_bench/` 目录启动：

```bash
bash opsd_env/run.sh                 # srun 分配资源，进入 /workspace
bash opsd_env/run.sh smoke           # 单卡短测试，无正式训练/裁判费用
bash opsd_env/run.sh audit           # wheel RECORD 文件完整性检查
bash opsd_env/run.sh python --version # 在分配的环境中执行命令
```

正式训练入口是 `/workspace/train_capped.sh`，要求设置 `SEED`、`OUT_DIR`；固定 100 个 optimizer steps，不能把冒烟测试当作正式成绩。

开发评估入口 `/workspace/dev_eval.sh CHECKPOINT OUTPUT_JSON` 已适配 Slurm GPU
编号，默认完整 30 题 × 12 次采样。原 `run.sh verify` 因上游清理函数可能误杀
其他作业的 GPU 进程而禁用；完整五次重训的正式验证器还需要安全适配。
部署代码已迁入 `AReaL-RSI/examples/scale_rsi_bench/opsd_env`，运行资产仍在本目录。

目录：`source` 固定版本 benchmark；`workspace` 实验代码和输出；`.venv`
独立 Python 依赖；`models` 模型链接；`cache/huggingface` 必需的模型/数据缓存；
`assets` 评测数据；`logs` 安装和验证记录。不要删除 Hugging Face 缓存，模型链接依赖它。

环境使用镜像的 Python 3.12，不与镜像内的 torch/vLLM 混用。
这是 Slurm 开发环境，不是隔离的官方 sandbox：绑定了 /storage，且本集群 GPU
不一定与官方 H100 相同。任务约束见 `workspace` 内的官方说明。

只有 `logs/dependency-integrity.json` 和 `logs/SMOKE_PASS.json` 均显示通过，
才表示完成本地环境验证；它们不是官方 benchmark 分数。
