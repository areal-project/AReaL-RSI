# nanoGPT 数据筛选 benchmark

统一启动入口为 `bench.sh`。需要已准备好的 Slurm / Singularity 环境；
依赖、固定上游源码、语料池和工作区默认位于仓库同级的
`scale_rsi_bench/nanogpt_env/`。可用 `RSI_WORK_ROOT` 指定运行目录的父目录，
或用 `RSI_ROOT` 指定本任务的运行目录。

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
