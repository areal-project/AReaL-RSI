# nanoGPT 数据筛选 benchmark

统一启动入口为 `bench.sh`，支持本机单 GPU 和 Slurm / Singularity 两种执行方式。
原有 Slurm 入口保持默认行为；本机工作区使用 `RSI_NANOGPT_BACKEND=local`。
依赖、固定上游源码、语料池和工作区默认位于仓库同级的
`scale_rsi_bench/nanogpt_env/`。可用 `RSI_WORK_ROOT` 指定运行目录的父目录，
或用 `RSI_ROOT` 指定本任务的运行目录。

## 本机四 GPU 工作区

在仓库根目录准备环境（无需 Slurm、SIF 或 Docker）：

```bash
python examples/scale_rsi_bench/nanogpt_env/scripts/prepare_local.py --gpus 0,1,2,3
```

此命令安装独立虚拟环境，固定上游源码，下载 tokenizer 和语料，重建并校验
语料池，然后创建 `workers/worker-0` 至 `worker-3`。准备阶段需要网络；
实验阶段默认使用离线缓存。可用 `--root /absolute/runtime/path` 改运行目录。

默认共享目录为仓库同级的 `scale_rsi_bench/nanogpt_env/`。虚拟环境、固定源码、
数据和 tokenizer 缓存共用；每个工作区有独立的 `workspace/`、`experiments/`、
日志、临时文件及 Triton/Torch 缓存。`workers.json` 记录物理 GPU UUID，
每个工作区固定一张卡，同一张卡的本地 benchmark 作业互斥；这些工作区用于
并行实验，不是文件系统或网络安全沙箱。

例如在 worker-0 上启动随机基线短程验证：

```bash
RUNTIME=/absolute/path/to/scale_rsi_bench/nanogpt_env
bash "$RUNTIME/workers/worker-0/run.sh" --baseline --smoke
```

任务在后台执行，返回 `run_dir`、PID、GPU UUID 和日志路径。查询结果：

```bash
bash "$RUNTIME/workers/worker-0/run.sh" status /absolute/run_dir
```

每个 Agent 的工作目录应设置为对应的 `workers/worker-N/workspace/`，并在
启动其进程时加载 `workers/worker-N/env.sh`，使文档分析代码也只看到分配的卡。
在工作目录中可直接执行：

```bash
bash ../run.sh submission/selection.json --smoke  # 两步，不能作为成绩
bash ../run.sh submission/selection.json          # 完整 DEV，3000 步
```

手动调用统一入口时使用 `--backend local --gpu 0`，并预先设置 `RSI_ROOT`。
每次调用均生成独立输入快照和结果，单次训练上限为 2 小时；并发占用同一卡时
新任务会以 `FAILED` 结束并说明原因。训练器保持固定上游版本，修改工作区里的
训练脚本不会改变正式 DEV 执行配方。`dev_eval.sh` 在本地工作区作为异步提交入口，
应按返回的 `run_dir` 查询完成状态。

## 启动随机基线

从仓库根目录运行：

```bash
bash examples/scale_rsi_bench/nanogpt_env/bench.sh --baseline
```

基线将语料池全部 182,016 个文档 ID 用 Python `random.Random(1337)`
打乱，按顺序打包前 12M tokens；它是从固定语料池随机选择文档，
不是生成随机 token。训练器和超参数固定为 3000 步、seed 1337，
在公开 dev 集评估，PPL 越低越好。

每次提交申请 1 GPU、16 CPU、64 GB 内存、2 小时作业上限。
提交为异步操作，返回 job ID 后应查看本次状态：

```bash
bash examples/scale_rsi_bench/nanogpt_env/bench.sh status /absolute/path/to/run_dir
```

`run_dir` 来自提交输出；本次输入、配置、日志、状态和结果写入该目录。
完整评估的结果应为 `mode=dev`、`max_iters=3000`、
`token_count=12000000`，同时检查作业退出状态。

## 随机基线文件与自定义输入

仅生成随机基线输入，不提交训练：

```bash
python examples/scale_rsi_bench/nanogpt_env/baselines/random/curate.py --output /absolute/path/selection.json
```

通过同一入口提交自定义选择：

```bash
bash examples/scale_rsi_bench/nanogpt_env/bench.sh /absolute/path/selection.json
```

输入必须是非空 JSON 整数数组，ID 范围为 `0..182015`，不重复，
累计文档 token 数须足够打包 12M tokens。数组顺序就是打包顺序。

两步环境检查使用：

```bash
bash examples/scale_rsi_bench/nanogpt_env/bench.sh --baseline --smoke
```

`--smoke` 使用短 dev 切片，结果不能作为完整 benchmark 成绩。

## 环境文件

`scripts/` 保存启动入口依赖的提交、容器、数据准备和固定打包代码；
`requirements-nanogpt.txt` 固定训练依赖。
固定上游版本为 `3061df5c8fa466830fca8c82fe2b7a1f14708b62`。
运行所需的 `source/`、`.venv/`、`pool/`、tokenizer 缓存和
`workspace/data/` 位于外部运行目录，不随 Git 分发。
GPU 训练通过 Slurm 提交；镜像内虚拟环境须在对应容器中使用。
