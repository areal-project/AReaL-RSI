# v0.2.2：保留独立探索的 RSI 指导

v0.2.2 只修改 leader 的角色 Prompt。Researcher Prompt、工具权限、非阻塞消息、
运行流程、计时和评测沿用 v0.2.1。新工作台研究和通用 KernelBench `prepare()` 默认
选择 v0.2.2；显式提供的配置和历史研究保持原选择。

## Prompt 的调整

- 已保存最好候选后继续研究，不让多人反复确认同一版本或提前等待收尾。
- 分享能解决具体困难的经验及适用条件，不把当前领先实现当成全员默认路线。
- 计划关注尚未弄清的问题和值得验证的思路，帮助各人设计能区分不同解释的实验。
- 区分观察和解释，附上原始记录；允许研究员有依据地反驳建议，收到反证后修正判断。

完整正文只有一份：[v0.2.2 RSI Prompt](../src/areal_rsi/agents/rsi/leader_guidance_v022_prompt.py)。
[v0.2.1 RSI Prompt](../src/areal_rsi/agents/rsi/leader_guidance_prompt.py) 保持原文；
两版共用 [Researcher Prompt](../src/areal_rsi/agents/research/leader_guidance_prompt.py)。

## 选择版本

工作台提供三个选项：纯 pass@k、v0.2.1、v0.2.2。启动前可切换；启动后版本锁定。
启动 JSON 的 `mode` 都是 `passk`，`collaboration_modules` 分别为：

| 协作方式 | collaboration_modules |
| --- | --- |
| 纯 pass@k | `[]` |
| v0.2.1 | `["leader_guidance"]` |
| v0.2.2（新研究默认） | `["leader_guidance", "leader_guidance_v022"]` |

`leader_guidance` v3 保留 v0.2.1 的完整行为；新增的 `leader_guidance_v022` v1
依赖它，只替换 `rsi_prompt`，不复制调度器、工具或研究员模板。
运行记录仍保存模块版本、两份 Prompt 的摘要和配置指纹。v0.2.1 的指纹保持不变。
以后修改协作方式时增加新的版本选项，保留已有版本用于对照，不覆盖旧版 Prompt。

KernelBench 通用准备入口可以显式选择旧版：

```python
prepare(..., mode="passk", collaboration_modules=("leader_guidance",))  # v0.2.1
prepare(..., mode="passk", collaboration_modules=())                    # 纯 pass@k
prepare(...)                                                            # v0.2.2
```

使用同版代码、模型、任务、预算和评测设置，仅切换协作选项，可以对照两版 leader Prompt。
`examples/kernelbench_artifacts/v0.2.1/` 批次示例明确选择 v0.2.1，仍为每名研究员半小时
有效时间。本次清理后已更新源码锁，角色 Prompt 和任务规则保持一致。
复现时应使用保存该示例的同一 Git 版本；仅选择相同协作方式并不能固定全部实验条件。
