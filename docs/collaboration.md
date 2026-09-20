# 协作配置与消融实验

新研究默认采用 **v0.2.4**，并可选择 v0.2.3、v0.2.2、v0.2.1 或纯 pass@k 做对照。
版本配置与实现见 [v0.2.4 说明](leader-guidance-v0.2.4.md)。

模型后端（standard / codex）和协作方式是两项独立配置。工作台的新建研究窗口可以选择
协作方式，研究页面的“协作方式”区域允许启动前修改，并支持“用此配置新建研究”。
这个按钮只复制协作选择；新研究的模型、任务和资源仍需单独确认。

## 当前可选项

| 基础流程 | 配置值 | 行为 |
| --- | --- | --- |
| 纯 pass@k | `passk` | 固定 k 名研究员独立完成整题，RSI 接收报告并督促继续，不提供建议或共享信息 |
| v0.2.1 · pass@k + RSI 协调 | `passk` + `leader_guidance` | 保留独立探索，RSI 查看原始证据，定向共享并发送非阻塞建议 |
| v0.2.1.1 · 区分问题与成果分享 | `passk` + `leader_guidance` + `problem_sharing` | 基于 v0.2.1，未解决的问题只增加一名接收者，已验证成果可分享给多人；研究员自主判断建议 |
| v0.2.2 · pass@k + RSI 协调 | `passk` + `leader_guidance` + `leader_guidance_v022` | 沿用 v0.2.1，仅调整 leader Prompt，关注未验证的思路和具体困难 |
| v0.2.3 · pass@k + RSI 协调 | `passk` + `leader_guidance` + `leader_guidance_v023` | 精简两个角色的 Prompt，保留定向共享、自主判断和持续探索 |
| v0.2.4 · pass@k + RSI 持续协调 | `passk` + `leader_guidance` + `leader_guidance_v024` | RSI 持续观察，自动展示研究员进展与实验，不依赖主动报告唤醒 |

工作台提供这些基础选项及下述 v0.2.5–v0.2.7 实验选项，新研究默认 v0.2.4。也可以直接选择模块组合。
共享 evidence pool 和审核尚未实现。更早的 `collaborative` 分工流程仅保留历史记录供查看、导出，不能启动或恢复。

完整 JSON 配置省略 `mode` 时为 `passk`，省略 `collaboration_modules` 时仍为空，
不会给显式导入的配置自动增加模块。旧研究保留启动时冻结的配置，不会被改成新版本。

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
不等待对方确认。接收者读完后用 `acknowledge_messages` 确认；未确认的消息会保留。
Codex 连续使用原生工具时，消息不会被强行插入。
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
本地工作台和其他 agent 工具端口不能直接访问，只放行本 actor 的工具桥接和配置的模型服务。
普通网页端口保持可用。运行时 `readable_paths` 与服务端点属于部署者的信任配置，
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
`standard` / `codex` 只影响 Runtime 和原生工具说明，不另复制一套协作角色 Prompt。

| 配置 | 流程与权限 | 角色正文来源 |
| --- | --- | --- |
| `passk` | `PassKEngine` + `PassKActions` | [RSI](../src/areal_rsi/agents/rsi/passk.md)、[researcher](../src/areal_rsi/agents/research/passk.md) |
| `passk` + `leader_guidance` | 同一个 `PassKEngine` + `GuidedPassKActions` | [RSI](../src/areal_rsi/agents/rsi/leader_guidance_v0.2.1.md)、[researcher](../src/areal_rsi/agents/research/leader_guidance_v0.2.1.md) |
| `passk` + `leader_guidance` + `problem_sharing` | v0.2.1 调度，增加问题传播限额及持久化记录 | [RSI v0.2.1.1](../src/areal_rsi/agents/rsi/leader_guidance_v0.2.1.1.md)、[researcher v0.2.1.1](../src/areal_rsi/agents/research/leader_guidance_v0.2.1.1.md) |
| `passk` + `leader_guidance` + `leader_guidance_v022` | 与 v0.2.1 相同 | [RSI v0.2.2](../src/areal_rsi/agents/rsi/leader_guidance_v0.2.2.md)，researcher 复用上一行 |
| `passk` + `leader_guidance` + `leader_guidance_v023` | 与 v0.2.1 相同 | [RSI v0.2.3](../src/areal_rsi/agents/rsi/leader_guidance_v0.2.3.md)、[researcher v0.2.3](../src/areal_rsi/agents/research/leader_guidance_v0.2.3.md) |
| `passk` + `leader_guidance` + `leader_guidance_v024` | 持续观察、自动进展视图、定向建议 | [RSI v0.2.4](../src/areal_rsi/agents/rsi/leader_guidance_v0.2.4.md)、[researcher v0.2.4](../src/areal_rsi/agents/research/leader_guidance_v0.2.4.md) |

角色正文集中放在 `agents/rsi/` 和 `agents/research/`。
`coordination/` 负责选择模板，统一通过 `areal_rsi.prompts.load_prompt()` 读取 Markdown。
文件布局和新增版本的方法见 [Prompt 文件管理](prompts.md)。

目前维护 **6 份 RSI 正文、5 份 researcher 正文**；另有一份公共 interaction Prompt。
运行时再填入人数、工作区、任务工具说明，并组合公共诚信规则、任务材料和 Runtime 调用说明。
这些公共部分不按模型或每个实验复制。公共 communication 模块的文字按角色引用；
不是每种模式都机械拼上同一整段。

`leader_guidance` 复用 pass@k 调度器，但替换两份角色正文及 Actions；
`leader_guidance_v022` 依赖它，只替换 RSI 正文；`leader_guidance_v023` 则替换两份角色正文。
这两个 Prompt 版本互斥。以后增加 evidence pool/审核时，优先增加简短的
模块说明和权限，避免为每个模块组合再复制一整套 Prompt。
v0.2.1 完整正文见 [Prompt 审阅](leader-guidance-prompt-review.md)，新版见
[v0.2.2](leader-guidance-v0.2.2.md) 和 [v0.2.3](leader-guidance-v0.2.3.md)。
基础 pass@k 版本为 2，`leader_guidance` 模块版本为 3，新增 Prompt 模块版本为 1。
v0.2.4 另加入持续观察的调度与进展视图，说明见 [v0.2.4](leader-guidance-v0.2.4.md)。
四种协调方式都可在当前代码中选择；更早实验若版本不匹配，仍须用原版本恢复。

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

实现入口在 `src/areal_rsi/coordination/collaboration.py`。每个 `CollaborationModule`
提供稳定 ID、版本、描述、支持的基础流程、依赖/冲突，以及 `apply(plan)`。
`CollaborationPlan` 包含 Actions 类、Engine 类、RSI/Research Prompt，以及 RSI 是否只做
监督。模块可用 `dataclasses.replace` 返回修改后的 plan，并尽量复用已有组件。

模块实现完成后在 `MODULES` 注册。工作台通过 bootstrap 自动读取目录，显示对应勾选项；
运行时按稳定依赖顺序组合。缺失依赖、不支持的基础流程、冲突、重复和循环依赖都会被拒绝。
不从用户 JSON 动态导入代码，也不会在浏览器单独维护一份可选模块清单。

当前 `coordination/leader_guidance.py` 增加协调 Actions，并从角色目录加载 Prompt，直接复用 PassKEngine。
实现 `evidence_pool` 时，还需明确哪些资料可见、哪些工具可用以及写入权限；
不能开放跨 run 访问，也不能解除独立工作区和候选验证的边界。
`mode` 保留基础流程的预算、生命周期和恢复语义，模块不能悄悄改变它们。

每个模块接入前测试实际工具权限、消息路径、Prompt、组合行为和恢复过程。改变模块或基础
流程的行为时提升相应版本；版本号需要开发者维护，Prompt 内容另有自动摘要检查。
纯 pass@k 的隔离测试必须继续通过，历史研究的读取不能改写原始记录。

## v0.2.5：有限共享

`passk_guided_v025` 在 v0.2.4 上增加 `sharing_quota`，默认选项仍是 v0.2.4。
同一经验只允许一名新增接收者，澄清和纠正沿用原经验；转述保留原来源。
研究员查询状态时不再自动收到同伴的报告，仍可按需读取本 run 的原始资料。
具体边界与实验口径见 [v0.2.5 说明](leader-guidance-v0.2.5.md)。

持续协调 Engine 的 `state_actions` 随模块组合更新，让会话恢复使用完整状态，
包括经验接收记录。常规状态查询与恢复不再维护两份不同的模块状态视图。

## v0.2.6：跟进指导结果

`passk_guided_v026` 在 v0.2.4 上增加 `intervention_review`，不叠加尚未通过复测的共享限额。
RSI 状态包含每人的最近六次正式结果和上次指导依据；发送建议时简要说明当前困难、
支持证据、待验证问题和上次建议的实际结果。研究员仍自行完成整题，工具权限不增加。
具体说明见 [v0.2.6](leader-guidance-v0.2.6.md)。默认选项仍为 v0.2.4。

## v0.2.7：回滚与新会话

v0.2.7 的 `passk_guided_v027` 独立组合 `leader_guidance`、`leader_guidance_v024` 和
`researcher_restart`。每题最多重启一半研究员，每人一次，保留当前最佳；恢复自己的历史候选
或公开起点，清空会话但沿用原预算。未叠加共享限额或决策记录。见 [v0.2.7](leader-guidance-v0.2.7.md)。

## 任务评测与协作流程的边界

RSI 核心只使用任务声明的指标、比较方向、正式分数和候选有效性，不理解题号、参考实现、
运行毫秒数或随机种子。任务适配器负责校准、计分、独立复测以及对应的任务规则和说明。
这些设置进入该研究的验证身份与启动材料，不加入通用 leader/researcher 角色 Prompt。

因此更换评测口径不需要新增协作模式，也不需要在调度器中判断具体 benchmark。
例如 [KernelBench 适配器](../examples/kernelbench_artifacts/README.md#探索评分与最终复测)
可以固定探索期间的 reference，仍向 RSI 提交普通的正式分数。
