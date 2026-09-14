# KernelBench 单题接入

直接优化各题的 `ModelNew`，通过已有 GPUQ 队列调用原评分器，无需额外的 Task 模型。工作台启动方式见[README](../../README.md)。

操作员为本 run 的每道题注册独立任务，将队列 budget 与 manifest 的 `submission_budget_per_task` 设为相同上限，例如 10。manifest 固定题号、任务 ID、workspace、SSH/Slurm 环境和评分身份；token 保留在远端，不放入候选。

| `client.py` 操作 | 用途 |
| --- | --- |
| `evaluate` | 提交冻结的 Python 候选并等待正式结果，消耗一次额度 |
| `diagnose` | 提交诊断脚本，共用每题额度；诊断日志不算正式成绩 |
| `status` | 查看每题用量与最近反馈，不提交 GPU |
| `collect` | 收集已评测的最佳正确源码，校验哈希并保留来源 |
| `verify` | 只读核对候选集合与原始评测记录，生成可信结果 |

```bash
uv run python examples/kernelbench_artifacts/client.py status --manifest /operator/run.json
uv run python examples/kernelbench_artifacts/client.py --help
```

将 `evaluate` 注册为 ApprovedCommand，参数为 `workspace / output / task / candidate`；`diagnose` 另有 `script`。文件路径相对冻结工作区，`run_job(wait=true)` 等待后返回公开 feedback。所有 GPU 诊断和正式评测计入同一每题额度；恢复沿用原 Job ID，不重复提交。查看环境时使用 manifest 指定的实际容器，SSH 登录主机不一定是评分环境。

候选集合按 `<题号>/model_new.py` 保存。`collect` 可用 `--exclude-sha256` 排除不符合任务语义的源码，但保留历史记录和已用次数。`verify` 配为可信验证器，使用 `content` 身份、`public_output_subdir="public"` 和 `final_candidate_policy="multiple"`；它只读取已有成绩，不查询新的隐藏题池。

search 使用同一源码已测正确的最佳速度；final 要求至少两次独立正式记录并使用最后一次。未评测或源码不匹配的题目计 0，主指标是固定题目集合中正确且 speedup > 1 的比例。需在原额度内预留复测，分数不能替代源码语义审查。

已有单题研究可用 `verify_existing.py --manifest <fixed-manifest> --workspace <snapshot> --output <new-output> --phase search|final` 接入。manifest 需固定原 task、题号、seed、评分 worker 哈希及协议；候选根目录保存 `model_new.py`。该入口不注册任务或提交 GPU；final 同样要求两条独立正式记录和严格 FP32 结果。
