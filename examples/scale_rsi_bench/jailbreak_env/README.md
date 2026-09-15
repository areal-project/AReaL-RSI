# Jailbreak Robustness

独立环境，复用镜像 `/storage/openpsi/images/areal-dev-sglang-0821.sif`。
默认资源：1 GPU、16 CPU、64G、4 小时；通过 `RSI_GPUS/RSI_CPUS/RSI_MEM/RSI_TIME` 覆盖。

从 `scale_rsi_bench/` 目录启动：

```bash
bash jailbreak_env/run.sh                 # srun 分配资源，进入 /workspace
bash jailbreak_env/run.sh smoke           # 单卡短测试，无正式训练/裁判费用
bash jailbreak_env/run.sh audit           # wheel RECORD 文件完整性检查
bash jailbreak_env/run.sh python --version # 在分配的环境中执行命令
```

开发评估的裁判通过环境变量配置：`OPENAI_API_KEY`（必需）、
`OPENAI_BASE_URL`（默认 `https://api.openai.com/v1`）、
`JAILBREAK_JUDGE_MODEL`（默认 `gpt-5-nano`）、
`JAILBREAK_JUDGE_CONCURRENCY`（默认 `32`）。命令行参数优先于环境变量。
自定义裁判模型的分数不能直接与官方分数比较；上游正式验证器仍保留原配置。
密钥应在仓库外保存，仅在裁判作业中加载。配置本身不发起付费请求。
模板见代码目录的 `jailbreak_env/judge.env.example`，完整命令见统一 `RUN_GUIDE.md`。
`workspace/baseline` 的空标记不是训练好的 checkpoint。

目录：`source` 固定版本 benchmark；`workspace` 实验代码和输出；`.venv`
独立 Python 依赖；`models` 模型链接；`cache/huggingface` 必需的模型/数据缓存；
`assets` 评测数据；`logs` 安装和验证记录。不要删除 Hugging Face 缓存，模型链接依赖它。

环境使用镜像的 Python 3.12，不与镜像内的 torch/vLLM 混用。
这是 Slurm 开发环境，不是隔离的官方 sandbox：绑定了 /storage，且本集群 GPU
不一定与官方 H100 相同。任务约束见 `workspace` 内的官方说明。

只有 `logs/dependency-integrity.json` 和 `logs/SMOKE_PASS.json` 均显示通过，
才表示完成本地环境验证；它们不是官方 benchmark 分数。
