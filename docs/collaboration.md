# 协作配置与消融实验

新研究默认采用 **v0.2.2**，并可选择 v0.2.1 或纯 pass@k 做对照。
版本配置与 Prompt 差异见下文。

模型后端（standard / codex / runtime）和协作方式是两项独立配置。工作台的新建研究窗口可以选择
协作方式，研究页面的“协作方式”区域允许启动前修改，并支持“用此配置新建研究”。
这个按钮只复制协作选择；新研究的模型、任务和资源仍需单独确认。

## 当前可选项

| 基础流程 | 配置值 | 行为 |
| --- | --- | --- |
| 纯 pass@k | `passk` | 固定 k 名研究员独立完成整题，RSI 接收报告并督促继续，不提供建议或共享信息 |
| v0.2.1 · pass@k + RSI 协调 | `passk` + `leader_guidance` | 保留独立探索，RSI 查看原始证据，定向共享并发送非阻塞建议 |
| v0.2.2 · pass@k + RSI 协调 | `passk` + `leader_guidance` + `leader_guidance_v022` | 沿用 v0.2.1，仅调整 leader Prompt，关注未验证的思路和具体困难 |

工作台提供以上三个快捷选项，新研究默认 v0.2.2。也可以直接选择模块组合。
共享 evidence pool 和审核尚未实现。更早的 `collaborative` 分工流程仅保留历史记录供查看、导出，不能启动或恢复。

完整 JSON 配置省略 `mode` 时为 `passk`，省略 `collaboration_modules` 时仍为空，
不会给显式导入的配置自动增加模块。旧研究保留启动时冻结的配置，不会被改成新版本。

## guidance 版本差异

v0.2.2 仅修改 leader Prompt；researcher Prompt、工具权限、非阻塞消息、调度、计时和评测
沿用 v0.2.1。新工作台研究和通用 KernelBench `prepare()` 默认选择 v0.2.2。

- 已保存最好候选后继续研究，不让多人反复确认同一版本或提前等待收尾。
- 分享能解决具体困难的经验及适用条件，不把领先实现作为全员默认路线。
- 计划关注未解决的问题，帮助研究员设计能区分不同解释的实验。
- 区分观察和解释，引用原始记录；收到有依据的反证后修正建议。

```python
prepare(..., mode="passk", collaboration_modules=())                    # 纯 pass@k
prepare(..., mode="passk", collaboration_modules=("leader_guidance",))  # v0.2.1
prepare(...)                                                            # v0.2.2
```

[固定 v0.2.1 批次示例](../examples/kernelbench_artifacts/v0.2.1/README.md) 每名研究员使用
半小时有效时间。角色源码与模块版本见下方表格；严格复现还须固定代码、模型和评测条件。

## RSI 协调的边界

研究员各自完成整题，在关键进展或持续受阻时报告，报告后继续探索。
RSI 用 `update_plan` 保存简短的全局研究计划，记录各人的路线、困难和下一步。
计划进入 RSI 状态和会话恢复上下文；`get_state` 同时提供各人的最近报告和最近一次建议。
收到新证据后重新判断方向，而不是按暂时排名要求大家复制领先方案。
RSI 复用已有读取工具查看报告、正式结果、候选文件和公开作业反馈；
`read_researcher_trace` 按页读取原生会话中的消息和工具调用，保存带来源的原始片段。

`advise_researcher` 将明确的建议、依据和原始记录引用发送给一名研究员。
引用用于定位证据，不再作为逐条读取授权。同一 run 内，研究员可以按需使用
`read_record`、`read_candidate_file`、作业反馈工具和 `read_researcher_trace` 查阅同伴资料。
`get_state` 提供团队成员和最近报告的入口；不自动将所有同伴轨迹注入上下文。
研究员只操作自己的工作区，采纳或复用后自行验证；不能切换同伴候选、改写同伴文件、
控制同伴作业，或把同伴的验证回执作为自己的成绩。
`import_candidate` 可以将同一 run 的候选复制到自己的新子目录，不覆盖已有文件；
纯 pass@k 仍不能读取或导入其他人的候选。

建议沿用持久化收件箱，在下一次 RSI 工具返回或下一回合送达，不取消当前执行，
不等待对方确认。Codex 连续使用原生工具时，消息不会被强行插入；送达记录沿用现有事件。
RSI 不新增、替换或停止研究员，不覆盖其代码，也不要求所有人采用同一路线。
固定 k、停止条件、独立验证和最终复测均沿用 pass@k。

研究员中途结束回合后，调度器在下一次 tick 自动发出不含技术建议的继续提醒，
沿用原会话、原节点和原预算。RSI 忙于调研或处理报告时也不会让研究员空等。
等待作业或已经停止的研究员不会收到这种提醒。RSI 仍可手动提醒继续。

KernelBench 的 `prepare(..., mode="passk", collaboration_modules=("leader_guidance",))`
同步生成该模式的任务说明和 RULES.md，避免仍带有纯 pass@k 的禁止共享条款。

## 跨 run 隔离

所有模式的 leader/researcher 都不得读取其他 run 的内容，包括历史研究、baseline
实现、会话、日志和成绩；同 run 分享不改变这条边界。公共论文与技术文档仍可查询，
任务明确提供的参考材料仍可使用。

记录工具只解析当前 run 的公开记录，拒绝其他 run URI、路径穿越和越界符号链接。
原生工具在进程沙箱中只获得自己的会话/工作区、已安装运行库和受信任公共依赖的读取权限；
网络隔离取决于后端：DSH/Claude 为动态 MCP 桥允许 localhost 通信，不能依赖端口规则
阻止其连接其他本地服务；RSI 工具仍检查会话令牌和角色权限。Codex/Hermes 按配置开放
工具桥和模型端口。普通网页端口保持可用，详细边界见[后端说明](agent-backends.md#隔离边界)。
运行时 `readable_paths` 与服务端点属于部署者的信任配置，
不能指向历史研究数据或不受约束的数据代理。

GPU 计算也使用同一边界。`examples/kernelbench_artifacts/queue_sandbox.py` 在队列现有
mount/PID namespace 内增加私有根目录、网络隔离和权限收缩；只暴露当前作业、
本任务编译缓存、公共库和题目，阻止直接读取其他 run 或访问队列接口。
同一个入口同时覆盖候选与诊断，评分器和调度流程保持原样。

准备实验时要求 `filesystem-network-v1` 能力；对于现有 GPUQ，校验其每次作业调用的
`sandbox_exec.py` 与仓库脚本的 SHA256 一致，无需重启共享队列。部署者仍负责保证
队列实际启用该入口，并验证节点上的 GPU 兼容性；不能只设置一个能力标记。
缺少能力或代码不匹配时拒绝准备；旧 manifest 不能用于提交候选或诊断。
部署和边界说明见 [GPU 队列隔离](gpu-queue-isolation.md)。

## Prompt 如何选择与组合

启动研究时，`build_plan(mode, modules)` 一次性选定 Actions（工具权限）、Engine
（调度/生命周期）和两个角色 Prompt。工作台的选项是这一配置的快捷入口。
`standard` / `codex` / `runtime` 只影响 Runtime 和原生工具说明，不另复制一套协作角色 Prompt。

| 配置 | 流程与权限 | 角色正文来源 |
| --- | --- | --- |
| `passk` | `PassKEngine` + `PassKActions` | [RSI](../src/areal_rsi/infra/agents/rsi/passk_prompt.py)、[researcher](../src/areal_rsi/infra/agents/research/passk_prompt.py) |
| `passk` + `leader_guidance` | 同一个 `PassKEngine` + `GuidedPassKActions` | [RSI](../src/areal_rsi/infra/agents/rsi/leader_guidance_prompt.py)、[researcher](../src/areal_rsi/infra/agents/research/leader_guidance_prompt.py) |
| `passk` + `leader_guidance` + `leader_guidance_v022` | 与 v0.2.1 相同 | [RSI v0.2.2](../src/areal_rsi/infra/agents/rsi/leader_guidance_v022_prompt.py)，researcher 复用上一行 |

角色正文集中放在 `agents/rsi/` 和 `agents/research/`。
`coordination/` 负责选择和加载模板，不内嵌角色正文。

目前维护 **3 份 RSI 正文、2 份 researcher 正文**；另有一份公共 interaction Prompt。
运行时再填入人数、工作区、任务工具说明，并组合公共诚信规则、任务材料和 Runtime 调用说明。
这些公共部分不按模型或每个实验复制。公共 communication 模块的文字按角色引用；
不是每种模式都机械拼上同一整段。

`leader_guidance` 复用 pass@k 调度器，但替换两份角色正文及 Actions；
`leader_guidance_v022` 依赖它，只替换 RSI 正文。以后增加 evidence pool/审核时，优先增加简短的
模块说明和权限，避免为每个模块组合再复制一整套 Prompt。
Prompt 正文以上表链接的源码为准，文档不再复制正文。
基础 pass@k 版本为 2，`leader_guidance` 模块版本为 3，新增 Prompt 模块版本为 1。
两种协调方式都可在当前代码中选择；更早实验若版本不匹配，仍须用原版本恢复。

## 保存和复现

预算可以使用原有墙钟时间，或[每名研究员的有效时间](effective-time.md)。计时方式独立于
协作模块；纯 pass@k 和 RSI 协调模式可以使用相同的有效时间口径做对照。

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
`{"mode":"passk","modules":[]}`。模型调用、Token、验证和节点
数量上限默认均为 null。完整 JSON 导入仍要求调用者明确
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

实现入口在 `src/areal_rsi/infra/coordination/policies/collaboration.py`。每个 `CollaborationModule`
提供稳定 ID、版本、描述、支持的基础流程、依赖/冲突，以及 `apply(plan)`。
`CollaborationPlan` 包含 Actions 类、Engine 类、RSI/Research Prompt，以及 RSI 是否只做
监督。模块可用 `dataclasses.replace` 返回修改后的 plan，并尽量复用已有组件。

模块实现完成后在 `MODULES` 注册。工作台通过 bootstrap 自动读取目录，显示对应勾选项；
运行时按稳定依赖顺序组合。缺失依赖、不支持的基础流程、冲突、重复和循环依赖都会被拒绝。
不从用户 JSON 动态导入代码，也不会在浏览器单独维护一份可选模块清单。

当前 `infra/coordination/policies/leader_guidance.py` 增加协调 Actions，并从角色目录加载 Prompt，直接复用 PassKEngine。
实现 `evidence_pool` 时，还需明确哪些资料可见、哪些工具可用以及写入权限；
不能开放跨 run 访问，也不能解除独立工作区和候选验证的边界。
`mode` 保留基础流程的预算、生命周期和恢复语义，模块不能悄悄改变它们。

每个模块接入前测试实际工具权限、消息路径、Prompt、组合行为和恢复过程。改变模块或基础
流程的行为时提升相应版本；版本号需要开发者维护，Prompt 内容另有自动摘要检查。
纯 pass@k 的隔离测试必须继续通过，历史研究的读取不能改写原始记录。

## 任务评测与协作流程的边界

RSI 核心只使用任务声明的指标、比较方向、正式分数和候选有效性，不理解题号、参考实现、
运行毫秒数或随机种子。任务适配器负责校准、计分、独立复测以及对应的任务规则和说明。
这些设置进入该研究的验证身份与启动材料，不加入通用 leader/researcher 角色 Prompt。

因此更换评测口径不需要新增协作模式，也不需要在调度器中判断具体 benchmark。
例如 [KernelBench 适配器](../examples/kernelbench_artifacts/README.md#探索评分与最终复测)
可以固定探索期间的 reference，仍向 RSI 提交普通的正式分数。
