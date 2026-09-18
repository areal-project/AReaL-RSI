# 协作配置与消融实验

模型后端（standard / codex）和协作方式是两项独立配置。工作台的新建研究窗口可以选择
协作方式，研究页面的“协作方式”区域允许启动前修改，并支持“用此配置新建研究”。
这个按钮只复制协作选择；新研究的模型、任务和资源仍需单独确认。

## 当前可选项

| 基础流程 | 配置值 | 行为 |
| --- | --- | --- |
| 纯 pass@k | `passk` | 固定 k 名研究员独立完成整题，RSI 接收报告并督促继续，不提供建议或共享信息 |
| pass@k + RSI 协调 | `passk` + `leader_guidance` | 保留独立探索，RSI 查看原始证据，定向共享并发送非阻塞建议 |
| 旧版协作流程 | `collaborative` | 保留原有分工、指导、消息、报告和证据共享流程 |

工作台提供以上三个快捷选项。新协调模式实际保存为 `mode=passk`、
`collaboration_modules=["leader_guidance"]`，也可以在纯 pass@k 下勾选“RSI 协调”。
旧版协作流程仍是单独的完整对照组。共享 evidence pool 和审核尚未实现。

原有配置保持兼容：省略 `mode` 时仍为 `collaborative`，省略 `collaboration_modules`
时为空。现有 pass@k 研究的行为保持不变。

## RSI 协调的边界

研究员各自完成整题，在关键进展或持续受阻时报告，报告后继续探索。
RSI 复用已有读取工具查看报告、正式结果、候选文件和公开作业反馈；
`read_researcher_trace` 按页读取原生会话中的消息和工具调用，保存带来源的原始片段。

`advise_researcher` 将建议和记录引用发送给一名研究员。消息本身保存读取授权：
接收者通过 `read_record` 读取这些记录，通过 `read_shared_file` 查看明确共享的候选文件。
记录中出现的其他引用不会自动获得授权。其他研究员仍看不到这些资料，也不能直接访问
同伴工作区、切换到同伴候选或复用其评测回执。

建议沿用持久化收件箱，在下一次 RSI 工具返回或下一回合送达，不取消当前执行，
不等待对方确认。Codex 连续使用原生工具时，消息不会被强行插入；送达记录沿用现有事件。
RSI 不新增、替换或停止研究员，不覆盖其代码，也不要求所有人采用同一路线。
固定 k、停止条件、独立验证和最终复测均沿用 pass@k。

KernelBench 的 `prepare(..., mode="passk", collaboration_modules=("leader_guidance",))`
同步生成该模式的任务说明和 RULES.md，避免仍带有纯 pass@k 的禁止共享条款。

## 保存和复现

完整研究 JSON 使用以下字段；其余任务、模型、命令和验证设置照常提供：

```json
{
  "mode": "passk",
  "collaboration_modules": []
}
```

工作台创建 API 可以在 `POST /api/runs` 提供：

```json
{
  "message": "",
  "collaboration": {"mode": "passk", "modules": []}
}
```

启动前修改使用 `POST /api/runs/<run_id>/collaboration`，请求体为
`{"mode":"passk","modules":[]}`。选择 pass@k 时，预设将模型调用、Token、验证和节点
数量上限设为 null，保留原有 k、时限、目标和执行资源。完整 JSON 导入仍要求调用者明确
配置符合 pass@k 的预算；无时限时必须有可信验证目标。

启动时保存：

- `coordination/started-config.json`：完整启动配置，包括 mode 和模块列表。
- `coordination/collaboration.json`：基础流程版本、模块版本、角色 Prompt 摘要和配置指纹。

启动后，包括暂停和停止后，都不能修改协作选择。恢复时检查当前实现的版本与角色 Prompt
是否匹配冻结记录；不一致时要求使用原版本恢复或新建研究。旧研究未记录过的版本不会
被补写，工作台会标明这是历史记录。

严格复现仍须使用同一 Git 提交，并固定任务材料、模型、推理强度、执行环境、评测协议和
预算口径。配置指纹是协作选择的标识，不代表整个实验环境，也不保证随机过程完全相同。

## 增加附加模块

实现入口在 `src/areal_rsi/coordination/collaboration.py`。每个 `CollaborationModule`
提供稳定 ID、版本、描述、支持的基础流程、依赖/冲突，以及 `apply(plan)`。
`CollaborationPlan` 包含 Actions 类、Engine 类、RSI/Research Prompt，以及 RSI 是否只做
监督。模块可用 `dataclasses.replace` 返回修改后的 plan，并尽量复用已有组件。

模块实现完成后在 `MODULES` 注册。工作台通过 bootstrap 自动读取目录，显示对应勾选项；
运行时按稳定依赖顺序组合。缺失依赖、不支持的基础流程、冲突、重复和循环依赖都会被拒绝。
不从用户 JSON 动态导入代码，也不会在浏览器单独维护一份可选模块清单。

当前 `leader_guidance.py` 只增加协调 Actions 和简洁的角色 Prompt，直接复用 PassKEngine。
实现 `evidence_pool` 时，还需明确哪些资料可见、
哪些工具可用以及写入权限；不能为开放一个共享池而解除全部跨尝试访问限制。
`mode` 保留基础流程的预算、生命周期和恢复语义，模块不能悄悄改变它们。

每个模块接入前测试实际工具权限、消息路径、Prompt、组合行为和恢复过程。改变模块或基础
流程的行为时提升相应版本；版本号需要开发者维护，Prompt 内容另有自动摘要检查。
旧流程的边界条件仍由其原有测试覆盖，纯 pass@k 的隔离测试必须继续通过。
