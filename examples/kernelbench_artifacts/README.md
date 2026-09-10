# 固定 KernelBench 题目的研究接入

此示例优化各题的 `ModelNew`，通过既有 GPUQ 队列执行原评分器。它不部署队列、不改变评分，也不调用额外的 Task 模型。

操作员在已有队列为每个 run、每道题注册独立任务，设置 `budget=11`，把 `remote.py` 和不含凭证的 manifest 放在该 run 的控制目录。任务 token 保留在远端对应 workspace 的父目录。manifest 固定题号、task ID、workspace、SSH/Slurm 入口、评分程序与题目集合的摘要；研究员不能重新注册或借用其他任务额度。

`client.py` 提供五个操作：

| 操作 | 用途 | GPU 提交 |
| --- | --- | --- |
| `evaluate` | 上传冻结工作区中的单个自包含 Python 候选，提交并等待正式结果 | 一次；幂等恢复不重复扣费 |
| `diagnose` | 上传 Python 诊断脚本及可选候选，运行逐层数值对照等 GPU 实验并取回日志 | 一次；与正式评测共用每题 11 次额度 |
| `status` | 查看各题用量、最佳速度和最近反馈 | 无 |
| `collect` | 从远端实际评测快照收集每题历史最佳正确源码及来源 | 无 |
| `verify` | 核对本地候选集合的源码哈希与真实评测记录，生成通用 `content` 验证结果 | 无 |

将 `evaluate` 配置为 `ApprovedCommand`，声明 `workspace / output / task / candidate` 四个参数，前两者由系统绑定。研究员通过 `run_job` 提交，`wait_for_job` 休眠等待完成，`read_job_output_file` 读取 `feedback.json`。反馈包含 `task` 题号；应结合任务参数和反馈确认评测归属，不能从 Job ID 猜测。远端排队、执行状态和每题额度写入作业的 `progress.json`。

将 `diagnose` 配置为 `diagnose_task` 命令，声明 `workspace / output / task / script / candidate` 参数。`script` 和可选 `candidate` 都是冻结工作区内的相对路径；仅运行参考实现时，`candidate` 传空字符串。远端工作区含公开题目 `reference.py`，候选统一保存为 `model_new.py`，脚本保存为 `_rsi_diagnostic.py`。脚本使用远端 Python/Torch 环境、1 张 GPU，最长运行 300 秒；提交前检查 Python 语法，输入总大小不超过 2 MB。所有输入随队列作业保存独立快照。

诊断结束后读取 `stdout.log`、`stderr.log` 和 `feedback.json`。日志各保留前 1 MB，是否截断及来源记录在 `diagnostic-logs.json`；远端完整日志不可读时回退到队列日志尾部并明确标记。`kind=run` 的诊断记录用于分析，即使脚本写出评分字段，也不会进入 `collect` 或 `verify` 的正式成绩。应先设计能定位问题的实验，再消耗剩余额度；诊断失败同样计数。

候选编译失败、正确性失败等是实际反馈，结束本地作业并保留结果。临时连接故障查询同一远端 Job ID，尚未拿到 ID 时复用同一提交意图；不能靠重复 CLI 调用恢复不确定的提交。任务额度用完会明确返回，不能持续重试第 12 次。所有 GPU 调试和正式评测共用该题额度，查询、等待、汇总不消耗额度。

用于汇总的候选目录按 `<题号>/model_new.py` 保存。`collect` 应先写入一个独立目录，再由 RSI 合并进自己的工作区并保存候选。`verify` 作为可信验证命令配置，输出 `evaluation.json`，公共反馈为 `public/feedback.json`，身份适配器使用 `content`。它从本地作业元数据绑定候选 ID 和内容摘要，通过本 run 的任务凭据只读查询原始队列记录，不接受 Agent 自报分数。

`search` 汇总每份源码已测正确的最佳速度；`final` 要求同一源码至少有两次独立正式记录，并使用最后一次结果。未评测、源码不匹配或不符合严格 FP32 规则的题目计 0。主指标是固定题目集合中正确且 `speedup > 1` 的比例；只有 8/8 才达到 `fast_1=1`。应在每题 11 次中预留最终复测，不得另加第 12 次。

测试覆盖提交幂等性、跨题拒绝、代码哈希绑定、最终复测、失败结果不能被旧成功掩盖，以及预算用完后终止。SSH/GPUQ 地址和可用 Slurm Job 由操作员提供，不能将示例环境当成永久可用的服务。
