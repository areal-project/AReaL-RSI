# KernelBench pass@k demo

只评测已有 kernel 代码，见 [代码评测最短指南](EVALUATE.md)。

复现 v0.2.1.1 的 Astra L1P63 单题实验，见 [v0.2.1.1 单题示例](v0.2.1.1/README.md)。

运行 Astra L3P10 + Luna L1P63/L1P77 三题并行、每题四名研究员各 30 分钟有效时间，
见 [v0.2.1 实验](v0.2.1/README.md)。该入口固定本次协作、Prompt、模型和评测设置。

默认运行 L3P10，固定四名研究员独立完成整题，探索限时一小时、GPU 提交不限次数。
默认模型为 Luna / xhigh，使用 v0.2.4 RSI 持续协调，观察进展、分享原始证据并发送非阻塞建议。
纯 pass@k 和 v0.2.1 仍可显式选择，见[协作方式](../../docs/collaboration.md)。

允许联网调研通用方法、官方文档和论文，禁止搜索现成答案或读取其他 run 的内容。
不联网对照可加 `--no-web-research`；恢复已有研究始终沿用原设置。

新建 Codex 研究默认使用 `execution_interface="cli"`：研究员通过原生 `exec_command` 执行
`kb eval` / `gpuq run`，命令在后台运行时用 `write_stdin` 取回结果，与 goal baseline 的使用方式一致。
见 [Agent CLI 指南](CLI.md)。报告、指导和证据查阅仍通过 RSI 工具，作业隔离和有效时间仍由 RSI 管理。
若需对照旧执行入口，调用 `prepare(..., execution_interface="tools")`；没有该配置的旧研究也沿用旧入口。
执行入口独立于协作模式，不改变 v0.1、v0.2.1 或后续版本的角色 Prompt。

## 快速启动

已有工作台时，批量实验使用统一入口。例如，重跑 exp-2 的组合，将两项协作研究升级为
v0.2.1.1，同时保留一项纯 pass@4 对照：

```bash
uv run --locked python examples/kernelbench_artifacts/batch.py \
  examples/kernelbench_artifacts/batches/v0.2.1.1.json --port 8198
```

三项均为 Astra / xhigh、4 名研究员各 1 小时有效时间：v0.2.1.1 的 L2P71、L1P63，
以及纯 pass@4 的 L2P71。默认使用原生 CLI 评测入口，沿用工作台的 API 和凭据。
脚本并行准备独立任务、校准 reference，然后在同一分组内并行启动；研究与最终复测期间
自动防止 macOS 闲置睡眠。运行前须确保工作台已部署所需代码。

以后只需复制并修改 JSON 中的题目、模型、协作方式和预算；`--group` 可覆盖组名。
每次执行会创建新批次。若只是恢复中断的启动，使用输出的批次文件：

```bash
uv run --locked python examples/kernelbench_artifacts/batch.py --resume /path/to/batch.json
```

重试沿用已保存的研究 ID、环境和配置，只启动尚未开始的研究，不重启已停止的研究，
也不重置预算。配置、角色 Prompt 和启动回执保存在工作台的 `operator/<batch_id>/`。
不需要每轮编写临时启动脚本或重启已经部署好的工作台。

先按[仓库启动指南](../../README.md#准备)安装依赖并填写 `.env`，确保 `ssh et15` 可用。[et15.json](et15.json) 提供当前集群入口；如果 Slurm 作业、队列地址或账号路径变化，复制一份修改并传入 `--environment /path/to/et15.json`。

`continue_script` 和 `python` 配置远端控制入口；GPU 诊断使用独立的 `gpu_python`（默认 `/opt/.venv/bin/python`），该路径须在 GPU 容器内存在。当前 et15 控制入口经登录节点访问双节点 GPUQ（`http://33.180.160.37:38472`），两台机器共 16 张卡；提交、查询及 GPU 执行均不为每次请求创建 Slurm step。统一队列由独立 CPU 作业维护，GPU 仍使用原来的两个作业；单个多卡任务最多使用同一节点的 8 张卡。已有研究保留创建时的队列配置。

在仓库根目录执行：

```bash
# 只检查 SSH、容器、GPUQ、题目与评分器摘要，不注册任务或消耗 GPU 额度
uv run --env-file .env --locked python run.py --type demo --check

# 正式启动；自动启动或复用 8197 端口的 RSI 工作台
uv run --env-file .env --locked python run.py --type demo
```

用 `--port 8200` 指定其他端口。端口被其他服务占用时报错；已有兼容的 RSI 工作台则复用。脚本输出研究链接，打开即可查看搜索图、任务、报告和成绩；不需要先与 Interaction 对话。

每次创建独立研究，从公开 reference 开始，不读取历史解法。中断后使用输出的 ID 恢复同一次研究，不重置时限：

```bash
uv run --env-file .env --locked python run.py --type demo --resume <run_id>
```

研究结束后自动选取每题未因具体反例撤回的最快正式正确源码，以 seed 137 复测；最终反馈不再用于优化。收尾适配器按通用选优清单中的源码摘要排除已撤回版本，保留其历史成绩。记录位于 `<数据目录>/runs/<run_id>/`，最终源码在 `finalization/output/candidate/<题号>/model_new.py`，分题成绩见工作台最终验证及 `verifications/`。准备配置位于 `setup/research.json`。

## 探索评分与最终复测

新研究使用 `fixed-reference-v1`。准备阶段先对每题 reference 做独立校准，默认取 3 批稳定
测量的均值中位数，随后整个研究共用这一分母。每批沿用原评分器的预热和 100 次计时；
批内标准差/均值及批间均值范围/中位数均须不超过 5%。不稳定批次会被跳过，默认最多
校准 6 批，仍未稳定则报错，不启动研究员。校准使用独立管理作业，不占探索提交次数或
研究员时间，成本与候选数量无关。配置在 [demo.json](demo.json) 的 `search_scoring` 中。

探索正式成绩为 `固定 reference 耗时 / 本次候选耗时`。即时反馈、成绩登记和最终选优
使用同一个计算函数 [scoring.py](scoring.py)，研究员工作区的 `baseline.json` 提供本次固定值。
评测仍逐次检查正确性；原始 reference 测量仍执行并保留为 `measured_ref_ms`、
`measured_speedup` 和 `measured_ref_stats`，供检查波动，不再作为探索计分的分母。

研究结束后，直接选正式探索成绩最好的有效候选，只对它提交一次 seed 137 独立复测。
不额外复测所有候选，也不再对同源码历史结果聚合中位数；撤回和超时结果仍排除。
最终分数沿用当次 reference/候选耗时，以保持与已有 baseline 最终评分的口径一致。
固定分母不能消除候选自身的测量噪声，因此最终确认仍有价值。

任务 manifest 冻结校准作业、样本、硬件软件环境、计分配置和协议摘要；准备恢复时复用
已有校准作业，最终提交也使用稳定的幂等键。远端校准与收尾运行该 run 冻结的控制脚本。
没有新评分配置的历史研究继续使用原分数，不补写校准，也不改写历史记录。

以上均属于任务评测适配器，RSI 核心和通用角色 Prompt 不包含这些规则。探索新口径不能
直接与旧研究的探索加速比混比；最终分数保持原协议。

## 通过 Interaction 做单题研究

在工作台新建研究，选择协作方式和模型后，说明题号、研究员数量、预算与评测环境。例如：

> 优化 L3P10，固定四名研究员，各自完成整题探索。探索限时一小时，提交不限次数，
> 最终复测另计。使用 KernelBench example 的 et15 环境，允许联网调研通用方法，
> 不搜索答案、不读取其他 run。请准备环境并总结配置，确认后启动。

准备脚本可由 Interaction 执行；替换本机仓库、工作台数据目录和当前研究 ID：

```bash
uv run --project <仓库目录> python <仓库目录>/examples/kernelbench_artifacts/single_task.py \
  --task L3P10 --run-id <当前研究ID> --data-root <工作台数据目录>
```

脚本会在启动前校准 reference，并输出配置路径。通过 `load_execution_config` 导入后，
保留工作台选择的协作方式。确认总结后回复“按此开始研究”。

用 `--time-limit 1800 --max-agents 4` 改为半小时墙钟探索预算，最终复测另计。
需要每名研究员分别计算有效时间的固定三题实验，请使用 [v0.2.1 入口](v0.2.1/README.md)。
