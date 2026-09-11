# Agent 实验入口

准备工作已经完成。入口为 `bench.sh`，可用绝对路径从任意宿主机目录调用。
输入必须是 JSON 格式的有序文档 ID 列表；不会修改输入文件或覆盖其他实验。

```bash
# 最短用法：指定数据选择，异步提交完整开发实验
bash /storage/openpsi/users/wanghaitao.wht/project/rsi/scale_rsi_bench/nanogpt_env/bench.sh /absolute/path/selection.json

# 调试提交链路：2 步训练 + 很小的开发集切片，不是 benchmark 分数
bash /storage/openpsi/users/wanghaitao.wht/project/rsi/scale_rsi_bench/nanogpt_env/bench.sh /absolute/path/selection.json --smoke

# 没有候选选择时，用确定性的随机选择跑基线
bash /storage/openpsi/users/wanghaitao.wht/project/rsi/scale_rsi_bench/nanogpt_env/bench.sh --baseline
```

每个作业申请 1 GPU、16 CPU、64GB 内存，Slurm 运行时限为 2 小时。
正式开发实验执行官方 3000 步、seed 1337，完成后提前退出；不会为了凑满两小时增加训练步数。
这是每个作业的上限，不是多个实验共享的 agent 总预算。训练器和超参数不能通过此入口修改。

提交成功时 stdout 是 JSON，包含 `job_id`、`run_dir`、`log` 和 `result` 路径。
退出码 0 只表示提交成功，需继续查询状态：

```bash
bash /storage/openpsi/users/wanghaitao.wht/project/rsi/scale_rsi_bench/nanogpt_env/bench.sh status /absolute/path/to/run_dir
tail -f /absolute/path/to/run_dir/job.log
```

`status` 同时查询 Slurm 和实验进程状态。`PENDING` 表示排队，`RUNNING` 表示正在执行；
`SUCCEEDED` 且 `result.json` 存在才表示成功。失败时读取 `worker.error`、Slurm 状态和 `job.log`。
`UNKNOWN` 通常表示 Slurm 记账暂未可见，可稍后再查。

每次实验位于 `nanogpt_env/experiments/<UTC时间>-<随机ID>/`，保存：

- `selection.json`：提交时的数据选择快照。
- `experiment.json`、`job.json`：实验配置和 Slurm job ID。
- `train.npy`：这次选择打包得到的 12M tokens。
- `job.log`、`state.json`：运行日志和阶段状态。
- `result.json`：成功后的结构化结果。

完整开发实验的结果包含 `mode: "dev"`、`evaluation_split: "dev"`、`heldout_ppl`、`train_s`、
`packing_s` 和 `total_s`。开发集困惑度越低越好；`official_score` 为 false，因为这不是最终 held-out 评分。
`mode: "smoke"` 的结果仅用于调试，不能用于排名或与完整实验比较。

入口直接从固定的 `pool_tokens.npy` 和 `pool_meta.npy` 打包，不重新分词；打包结果已经与官方 verifier 比对。
重复 ID、越界 ID、非整数 ID 会在提交前拒绝；不足 12M tokens 会在训练前拒绝。
所有实际训练均使用 `source/` 中的原版训练器，并在公开开发集上评估。

最终 held-out 评分仍使用原入口：将最终 `selection.json`、`curate.py` 和 `claim.md` 放到
任务要求的位置，再从 `rsi/scale_rsi_bench/` 执行 `bash nanogpt_env/scripts/slurm.sh verify`。
