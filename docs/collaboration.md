# 协作配置与消融实验

模型后端（standard / codex）和协作方式是两项独立配置。工作台的新建研究窗口可以选择
协作方式，研究页面的“协作方式”区域允许启动前修改，并支持“用此配置新建研究”。
这个按钮只复制协作选择；新研究的模型、任务和资源仍需单独确认。

## 当前可选项

| 基础流程 | 配置值 | 行为 |
| --- | --- | --- |
| 纯 pass@k | `passk` | 固定 k 名研究员独立完成整题，RSI 接收报告并督促继续，不提供建议或共享信息 |
| 旧版协作流程 | `collaborative` | 保留原有分工、指导、消息、报告和证据共享流程 |

旧版协作流程是完整对照组，不等于“pass@k 加一个 leader 协调模块”。本次没有新增
leader 协调、共享 evidence pool 或投票审核算法；附加模块列表目前为空。未实现的模块
会明确报错，不会保存一个没有实际效果的开关。

原有配置保持兼容：省略 `mode` 时仍为 `collaborative`，省略 `collaboration_modules`
时为空。现有 pass@k 研究的行为保持不变。

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

例如未来实现 `leader_coordination` 时，需要同时提供协调 Actions、必要的调度扩展和
简洁的角色 Prompt，而不只是改 Prompt。实现 `evidence_pool` 时，还需明确哪些资料可见、
哪些工具可用以及写入权限；不能为开放一个共享池而解除全部跨尝试访问限制。
`mode` 保留基础流程的预算、生命周期和恢复语义，模块不能悄悄改变它们。

每个模块接入前测试实际工具权限、消息路径、Prompt、组合行为和恢复过程。改变模块或基础
流程的行为时提升相应版本；版本号需要开发者维护，Prompt 内容另有自动摘要检查。
旧流程的边界条件仍由其原有测试覆盖，纯 pass@k 的隔离测试必须继续通过。
