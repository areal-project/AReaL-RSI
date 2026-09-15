# KernelBench 八题 demo

RSI 自主组织同时最多 5 名 Research Agent，直接优化八题的 kernel，调用 et15 上已部署的 GPUQ 评测；不需要 Task Agent。等待中的研究员也占名额，停止后可在研究中途创建新方向，累计创建数不限。默认 Astra / xhigh，每题 **10 次探索 + 1 次独立最终复测**，诊断和失败也计入额度，不设置总时间上限。

新研究默认允许联网调研通用方法、文档和论文，禁止读取本题现成解法。RSI 先调研再规划，受阻时结合实验结果补充调研。若要沿用 goal baseline 的不联网条件，在启动命令中加 `--no-web-research`；恢复已有研究始终沿用原设置。

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

## 通过 Interaction 做单题研究

在工作台新建研究，配置模型地址、密钥、`gpt-6-astra` 和 `xhigh`。然后告诉 Interaction：

> 本次只优化 L3P10，目标是正确前提下尽可能高的加速比，没有固定达标阈值。最多 3 名 Research Agent，全题共享 10 次探索 + 1 次最终复测，诊断和失败计数；总时间上限 1 小时，越快越好，不必用满。沿用 KernelBench example 的 et15 环境和 goal baseline 评分规则，允许联网调研通用方法，不查本题现成解法。请用当前研究 ID 运行下面的准备脚本，导入输出的配置，再总结需求给我确认。

准备脚本可由 Interaction 的 bash 执行；将下面两处目录替换为本机仓库和工作台数据目录：

```bash
uv run --project <仓库目录> python <仓库目录>/examples/kernelbench_artifacts/single_task.py \
  --task L3P10 --run-id <当前研究ID> --data-root <工作台数据目录>
```

确认总结后回复“按此开始研究”。测试其他题目只需替换题号；每次新建研究，额度独立。不联网对照需在需求中说明，并给准备脚本加 `--no-web-research`。计分使用单题 `speedup`，系统最多在第 57 分钟结束探索、预留最终复测时间，整个流程受 1 小时上限约束。

若要做 20 分钟、GPU 提交不限次数的研究，在需求中说明并给准备脚本加 `--time-limit 1200 --submissions unlimited --name L1P77-20分钟-不限次数`。此时可通过作业工具按需评测、调试和比较参数，无需逐次批准；仍保留全部作业记录和独立最终复测，系统在第 18 分钟结束探索并开始收尾。联网只能调研通用方法，禁止搜索答案或获取本题现成的完整实现。

只限制探索时间、最终复测另计时，加 `--time-scope exploration`；例如 `--time-limit 1800 --time-scope exploration --submissions unlimited --max-agents 6`。单题正式正确产物会自动归档、验证并更新工作台，不需要 agent 手动刷新成绩。

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

预算均为最多 10 次探索反馈加 1 次最终复测；探索 seed 42、最终 seed 137，每次 5 次正确性检查、100 次计时。表中历史实验未联网。原 baseline 两题受环境/API 阻塞影响，RSI 的共享时间安排和协作也不同，不能视作仅改变编排的严格消融。当前 demo 默认允许联网、不限总时间，并使用当前版本的角色 prompt；模型/API 延迟会影响耗时。
