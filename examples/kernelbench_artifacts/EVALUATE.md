# KernelBench 代码评测：最短指南

适用于当前 et15 上已部署的 GPUQ。请求经 SSH → 登录节点控制脚本 → 常驻 GPUQ → GPU 执行，**每次提交和查询都不新建 Slurm step**；GPUQ 仍依赖已申请的 Slurm GPU 资源，正式评测和 GPU 调试都会占用 GPU。

在本机已安装依赖的 RSI 仓库中执行，确保 `ssh et15` 可用。不需要模型密钥或启动工作台。

## 1. 准备题目（每个独立实验一次）

```bash
cd /Users/ran/dev/AReaL-RSI-simplify  # 换成你的仓库目录
KB_CASE=L3P10
KB_RUN=kb-manual-$(date +%Y%m%d-%H%M%S)
KB_DIR="$PWD/.rsi/kernelbench-manual/runs/$KB_RUN"

.venv/bin/python examples/kernelbench_artifacts/single_task.py \
  --task "$KB_CASE" --run-id "$KB_RUN" \
  --data-root .rsi/kernelbench-manual --submissions 10
```

这一步只登记任务、下载题目，不调用模型或执行 GPU 评测。题目在 `$KB_DIR/setup/public-inputs/tasks/$KB_CASE/reference.py`，候选代码需提供兼容的 `ModelNew`。需要不限提交次数时，把 `10` 改成 `unlimited`。

## 2. 提交代码

```bash
mkdir -p "$KB_DIR/candidate"
cp /你的路径/model_new.py "$KB_DIR/candidate/model_new.py"

.venv/bin/python examples/kernelbench_artifacts/client.py evaluate \
  --manifest "$KB_DIR/setup/gpuq-manifest.json" \
  --workspace "$KB_DIR/candidate" --candidate model_new.py \
  --task "$KB_CASE" --output "$KB_DIR/attempt-001"
```

命令自动提交、排队并等待结果。**修改代码后再次评测，要换成 `attempt-002` 等新输出目录**；同一输出目录用于继续查询同一次提交，不会重新评测。

## 3. 查看结果

结果在 `$KB_DIR/attempt-001/feedback.json`：

- `correct`：是否通过正确性检查。
- `speedup`：参考实现耗时 / 候选耗时，大于 1 才是加速。
- `evaluation`、`stderr_tail`：详细指标和错误信息。

当前正式评测使用 `strict-fp32-v1`：FP32、关闭 TF32，5 次正确性检查、100 次计时。失败和 GPU 调试都计入探索额度；本指南只运行单次代码评测，不自动启动研究或最终复测。

连接信息来自 [et15.json](et15.json)；服务迁移后更新该配置。当前入口是 `rsi_queue_control.sh`，不要改回每次调用 `slurm_continue_job.sh` 的方式。另一分支的 `benchmark/kernelbench` 中 `kb verify` 是独立接口，不用于这里的现有 GPUQ。
