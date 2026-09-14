# KernelBench 八题 demo

RSI 自主组织最多 5 名 Research Agent，直接优化八题的 kernel，调用 et15 上已部署的 GPUQ 评测；不需要 Task Agent。默认 Astra / xhigh，每题 **10 次探索 + 1 次独立最终复测**，诊断和失败也计入额度，不设置总时间上限。

## 快速启动

先按[仓库启动指南](../../README.md#准备)安装依赖并填写 `.env`，确保 `ssh et15` 可用。[et15.json](et15.json) 提供当前集群入口；如果 Slurm 作业、队列地址或账号路径变化，复制一份修改并传入 `--environment /path/to/et15.json`。

在仓库根目录执行：

```bash
# 只检查 SSH、容器、GPUQ、题目与评分器摘要，不注册任务或消耗 GPU 额度
uv run --env-file .env --locked python run.py --type demo --check

# 正式启动；自动启动或复用 8197 端口的 RSI 工作台
uv run --env-file .env --locked python run.py --type demo
```

用 `--port 8200` 指定其他端口。端口被其他服务占用时报错；已有兼容的 RSI 工作台则复用。脚本输出研究链接，打开即可查看搜索图、任务、报告和成绩；不需要先与 Interaction 对话。

每次默认创建独立研究，从公开 reference 开始，不读取历史解法。中断后使用输出的 ID 恢复同一次研究，不重置额度：

```bash
uv run --env-file .env --locked python run.py --type demo --resume <run_id>
```

研究结束后自动选取每题最快的正式正确源码，以 seed 137 复测；最终反馈不再用于优化。记录位于 `<数据目录>/runs/<run_id>/`，最终源码在 `finalization/output/candidate/<题号>/model_new.py`，分题成绩见工作台最终验证及 `verifications/`。准备配置位于 `setup/research.json`。

## Baseline 与 RSI 结果参考

下表为同一 L20X、固定八题与 strict-fp32-v1 评分下的历史记录；数字是最终正确版本相对 PyTorch reference 的加速比。新研究存在搜索波动，历史 RSI 两次分别为 **6/8、5/8 加速**，不是新 run 的保证值。

| 题目 | 原 Astra goal baseline | RSI 正式实验 | RSI 独立复跑 |
| --- | ---: | ---: | ---: |
| L1P63 | 未通过 | 3.308× | 0.977× |
| L2P14 | 未完成 | 0.664× | 0.630× |
| L3P10 | 未完成 | 未通过 | 未通过 |
| L3P28 | 未通过 | 8.326× | 1.850× |
| L1P77 | 0.206× | 4.164× | 8.652× |
| L2P71 | 0.122× | 1.628× | 2.705× |
| L2P80 | 0.223× | 1.545× | 1.505× |
| L3P19 | 0.021× | 1.067× | 1.084× |
| 正确 / 加速题数 | 4/8 · 0/8 | 7/8 · 6/8 | 7/8 · 5/8 |
| 总耗时 | 每题最多 30 分钟 | 约 41 分钟 | 约 88 分钟 |

预算均为最多 10 次探索反馈加 1 次最终复测；探索 seed 42、最终 seed 137，每次 5 次正确性检查、100 次计时。原 baseline 两题受环境/API 阻塞影响，RSI 的共享时间安排和协作也不同，不能视作仅改变编排的严格消融。当前 demo 不限总时间，并使用当前版本的角色 prompt；模型/API 延迟会影响耗时。
