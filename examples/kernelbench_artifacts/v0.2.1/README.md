# KernelBench v0.2.1

沿用 2026-09-18 23:25 启动的三题实验设置，将每名 Researcher 的有效时间改为 **30 分钟**。
三项研究同时运行，每项由一个 RSI Agent 协调四名独立探索的 Researcher。
原工作台分组为 **v0.2.1**，历史 run ID 中的 `v03` 保留；历史实验仍是每人一小时，
不受本示例预算调整影响。

## 实验设置

| 题目 | RSI 与 Researcher 模型 | 推理强度 | Researcher 数量 | 每人预算 |
| --- | --- | --- | --- | --- |
| L3P10 | gpt-6-astra | xhigh | 4 | 30 分钟有效时间 |
| L1P63 | gpt-5.6-luna | xhigh | 4 | 30 分钟有效时间 |
| L1P77 | gpt-5.6-luna | xhigh | 4 | 30 分钟有效时间 |

- 后端：Codex → AReaL Agent Runtime。Runtime `0.4.0.dev1+rsi1`，Codex CLI `0.153.4`。
  API 地址固定为 `https://4ipic.com/cliproxy/v1`，密钥由运行者提供。
- 协作：`passk` v2 + `leader_guidance` v3。四名研究员均独立完成整题；RSI 查看原始证据，
  定向分享同 run 的经验，并提出非阻塞建议。保持方向多样性，不按早期排名直接否定研究方向。
  不替换或追加研究员，不启用共享 evidence pool。
- 计时：`baseline-active-v1`。每人分别扣除 API 故障重试和 GPU 排队的并集；正常模型推理、
  API 延迟、调研、编译及 GPU 执行计时。RSI 随最后一名研究员停止，最终复测另计。
  因此整组实际历时可能超过半小时，不另设墙钟上限。
- 每题最多四个并发作业；不限制累计提交、模型调用或 token 数，不设达标即停的分数目标。
- 原生网页搜索开启，原生 subagent / goal 禁用。只允许调研通用资料，禁止搜索题目答案、
  读取外部 baseline 或其他 run 的任何内容。研究员只能通过研究工具访问本 run 同事的资料。
- NVIDIA L20X，`strict-fp32-v1`。探索 seed 42，最终 seed 137；每次五次正确性检查、
  一百次性能计时。禁止 `triton.autotune`、批量配置扫描或自动调参循环。
- 探索用 `fixed-reference-v1`：准备时每题收集三批稳定 reference，取批均值中位数。
  批内标准差/均值、批间范围/中位数均不超过 5%，最多尝试六批；不稳定则不启动研究。
  校准不占研究员预算。研究期间统一用该分母计分，正确性检查仍逐次执行 reference。
- 结束后按已有正式探索成绩选出一个有效候选，只做一次 seed 137 独立复测；最终分数用
  当次 reference/候选耗时。不会重新遍历所有候选，最终反馈也不再用于优化。

## 启动

本地使用支持 `sandbox-exec` 的 macOS、Python 3.11+、uv，远端使用同一套 et15/GPUQ
环境。需要能通过 SSH 访问远端容器入口、题库、评分器和队列管理员凭据文件。
[environment.json](environment.json) 保存本次实际使用的地址与路径：

```text
GPUQ:       http://33.180.160.37:38472
SSH:        et15
Slurm job:  958437
入口:       /storage/openpsi/users/miumiu.zwh/tools/rsi_queue_control.sh
```

在包含此示例的完整仓库根目录执行。使用同一版代码与锁定依赖；只复制这个子目录不足以运行：

```bash
uv sync --locked

# 只读检查：代码版本、SSH、GPUQ、硬件、题目和评分器；不调用模型或提交 GPU 作业
uv run --locked python examples/kernelbench_artifacts/v0.2.1/run.py --check
```

把模型密钥放在本机 `.env` 的 `RSI_API_KEY` 中，然后启动：

```bash
uv run --locked --env-file .env python examples/kernelbench_artifacts/v0.2.1/run.py
```

也可不用 `.env`，传入一个只有 `api_key` 字段的本机 JSON 凭据文件：

```bash
uv run --locked python examples/kernelbench_artifacts/v0.2.1/run.py \
  --credential-file /absolute/path/to/credentials.json
```

脚本固定模型、API 地址和推理强度，不受 `.env` 中同名设置影响。密钥只交给工作台
保存到各 run 的私有凭据文件，不写入示例或批次配置。

默认工作台为 **http://127.0.0.1:8199**，数据目录为仓库内 `.rsi/kernelbench-v0.2.1/`。
脚本准备三题、完成 reference 校准，然后在新组 **v0.2.1** 中并行启动研究，输出三个链接。
每次运行都会创建新的批次和 run ID，不复用历史答案。

需要其他端口或数据目录时使用 `--port 8200 --data-root /absolute/path/to/data`。
本机 SSH 别名不叫 et15 时加 `--ssh-host <别名>`；该别名必须连接同一远端环境。
队列地址与远端路径保持本例中的值。旧 Slurm 作业或队列失效时需先恢复环境，不能将
另一个评分环境下的运行直接视为这次实验的复现。

准备可能受 GPU 排队影响。等待脚本输出研究链接后，研究由后台工作台运行；关闭终端
或浏览器不会停止研究，但本机需要保持唤醒与网络连接。macOS 可另开终端运行
`caffeinate -i`，完成后结束该进程。

## 中断后继续

脚本一开始会输出 `v021_...` 批次 ID。准备或启动中断后，沿用原参数，加上该 ID：

```bash
uv run --locked --env-file .env python examples/kernelbench_artifacts/v0.2.1/run.py \
  --resume v021_<脚本输出的批次ID后缀>
```

如果首次指定了端口/数据目录，此处也需指定相同值；SSH 别名已保存在批次中，无需再传。
脚本复用已准备的 run、reference 校准和预算，只启动尚未启动的研究。正在运行或已经结束
的研究不会重新启动，手动暂停的研究也不会被自动恢复。不要同时对同一批次运行两个启动脚本。

## 文件与复现范围

- [experiment.json](experiment.json)：三题模型和研究参数。
- [environment.json](environment.json)：相同 GPUQ 地址、SSH 容器入口及远端路径；不含密钥。
- [run.py](run.py)：调用上层 `demo.prepare` 和工作台 API，没有另写研究流程或评分器。
- [source-lock.json](source-lock.json)：锁定本示例的代码和依赖，覆盖角色 Prompt、协作、计时、
  Runtime 与任务适配器。以原实验源码为基础，合入 main 的 Linux Runtime 和任务交接修复，
  并记录本次 1800 秒预算调整。若文件发生变化，脚本会拒绝以本版本启动；以后改实验应新建版本，
  不要更新此清单来绕过检查。历史实验的源码快照与一小时预算均保持不变。

Prompt 仍由仓库内的一套实现生成，不维护另一份副本：

- [RSI Prompt](../../../src/areal_rsi/agents/rsi/leader_guidance_v0.2.1.md)
- [Researcher Prompt](../../../src/areal_rsi/agents/research/leader_guidance_v0.2.1.md)
- [任务、工具说明与规则生成](../demo.py)、[基础任务规则](../PASSK_RULES.md)
- [协作与非阻塞通信](../../../docs/collaboration.md)、[有效时间](../../../docs/effective-time.md)

每批的配置及启动结果在 `<数据目录>/operator/<批次ID>/`；每题的冻结配置、校准和环境
在 `runs/<run_id>/setup/`。运行后的完整配置在 `coordination/config.json`，实际角色 Prompt
在 `agents/*/sessions/*/system-prompt.json`。最终产物在
`finalization/output/candidate/<题号>/model_new.py`。

复现的是相同输入、Prompt、流程、预算和评测规则。每次重新校准 reference，不复用旧实验
的测量值；模型输出、实时网页内容、API 波动及队列负载仍会变化，因此不保证相同轨迹或成绩。
