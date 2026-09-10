# 研究编排的精简设计

目标是让一组能独立工作的 Agent 协作研究。模型负责判断和研究，代码负责记录、等待、恢复和验证边界。

## 为什么需要精简

旧实验暴露的不只是表达风格问题：

- 工具把不断增长的历史节点、证据和环境说明反复交给模型，挤占当前问题的上下文。
- 任务、报告、继续指令要求模型填写多段重复内容、复制关联 ID，把记账变成了研究工作。
- 正常最终回复不算提交报告，研究员也无法查看 RSI 提交的作业状态；协议本身制造了失败和额外交接。
- 提示词过多强调检查、复核和限制，却没有清楚鼓励研究员自主完成有结论的尝试。没有实际测到的机制也可能被误判为无效。

本次减少这些负担，不增加新的管理角色或固定研究阶段。具体测量与行为局限见[验收记录](research-simplification-acceptance.md)。

## 职责与流程

- **Interaction**：通过对话整理目标、硬限制和自然语言建议；信息足够且用户已授权时交给 RSI。运行中的补充进入原会话。
- **RSI**：推进整体目标、决定研究方向、发起或停止研究员、协调发现。可以自行调研和做必要工作。默认一个方向，有实际并行价值时再增加。
- **Research**：自主完成一个方向中的完整尝试，包括所需调研、实现、排查和验证。通常连同所需验证完成一轮尝试后回复 RSI，然后等待下一条指令。方向内保留会话与工作区。

```text
用户与 Interaction 确定需求 → RSI 规划并派发任务
                               ↓
                   Research 自主完成本轮尝试
                               ↓
                    正常回复，系统归档并停止推理
                               ↓
                 RSI 决定继续、换方向、共享发现或结束
                               ↓
                     下一条任务进入原 Research 会话
```

节点仍叫 `ResearchIteration`，含义是一次通用研究任务。它可以没有候选、验证、分数或 `EvolutionResult`；起点和 baseline 也可以为空。消息、节点、证据、版本和会话均归属一个 run。

## 模型只处理有用的信息

任务和报告使用自然语言。报告说明做了什么、具体结果、结论的局限和下一步建议，篇幅随任务复杂度变化。旧的七段报告仍可读取，新报告只需 `message`，不再强制填栏目。

研究员的普通最终回复会自动成为报告。系统关联本轮工具产生的候选、证据、资料和作业结果；`assign` 自动关联上一份报告，不要求模型重抄 ID。完整报告保留在归档中，超过 4,000 字符的消息只发送首尾和原文引用。

`get_state` 返回当前方向状态、当前任务/报告引用和最近五条证据短摘要，不返回全部历史节点。`list_records` 每页查找十条记录，`read_record` 按需读取全文。工具输出保留中文，不转成大段 Unicode 转义。完整历史仍用于工作台与审计。

## 工具与运行边界

- 文件操作展示一套 DSH 工具；旧文件工具保留兼容执行，不再同时展示。接入完成后隐藏准备工具，实际权限与冻结检查仍由 handler 执行。隐藏工具不影响未完成调用的恢复。
- 作业状态与完成订阅在 run 内可共享。研究员可以等待 RSI 或其他方向的作业；完成事件进入原任务，不新建研究节点。
- 普通执行产物遵守所属方向/共享引用权限。验证器只开放操作员通过 `ApprovedCommand.public_output_subdir` 明确发布的子目录；未设置时仍只开放验证结果记录，不公开原始日志和私有文件。
- 模型调用、工具执行、消息确认分别持久化。等待不调用模型；临时 API 错误沿用同一逻辑请求自动重试。中断恢复沿用会话和操作身份。
- 起点身份和已有成绩直接提供给回合上下文，避免重复查找。相同起点内容的再次保存不改变它作为 baseline 输入的身份。
- ResearchConfig 默认不设置额外的单回合调用次数上限，run 的用户预算仍有效。显式配置的单回合限制继续支持。
- `finish` 先检查所需验证与 target，再自动停止研究员并结束 run；调用者不用逐个清理方向。没有达标的研究不能因此变成成功。

这些接口不提供进程安全沙箱。DSH shell 能访问其运行账户可访问的路径；不可信候选应使用隔离的执行后端。

## 代码边界

| 模块 | 负责内容 |
| --- | --- |
| `agents/*/prompt.py`、`agents/communication.py` | 角色职责与表达方式 |
| `api/research.py`、`coordination/store.py` | 记录模型与旧报告读取 |
| `coordination/coordinator.py` | 节点、消息与状态转换 |
| `coordination/views.py`、`actions.py` | 小型模型视图、查询与角色工具 |
| `coordination/engine.py`、`modules/responses.py` | 调度、连续会话、恢复、模型调用 |
| `coordination/jobs.py`、`execution/*` | 作业生命周期、公开反馈与可信成绩 |
| `ui/workbench*` | 读取完整归档、显示自然语言报告 |

## 验证方式

`tests/test_research_simplification.py` 覆盖 72 个节点、24 轮三方向交接、worker 重建、等待零调用、历史分页、旧报告、跨方向作业反馈及权限、完成前验证、隐藏工具的未完成调用恢复。它检验调度机制，不证明模型会合理研究。

`scripts/evaluate_research_team.py` 使用实际配置的模型和真实 DSH 工具，优化一个 CPU 在线缓存策略。成绩来自候选代码在六类访问负载上的实际命中率，不使用预设分数。可分别设置一个或三个研究员，观察派单、自主排查、报告、共享与收尾。它不是 KernelBench 成绩，也不是不可信代码安全测试。

```bash
uv run python scripts/evaluate_research_team.py --agents 3 --minutes 20
# 凭证从进程环境读取；也可显式指定本机 --env-file。
# 中断后接续同一测试：
uv run python scripts/evaluate_research_team.py --resume <run_id>
```

结果与原始轨迹写入 `.rsi/research-lab/runs/<run_id>/`。评估应分别报告模型研究质量、调度故障和 API 等待，不能用固定脚本通过来代替真实模型验证。

## 参考实现

- [OpenHands TaskManager](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-tools/openhands/tools/task/manager.py)：保留子任务会话，使用回复传递成果。
- [DeepAgents async subagents](https://docs.langchain.com/oss/python/deepagents/async-subagents)：按持久线程管理异步任务、更新与取消。
- [Codex followup_task](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/handlers/multi_agents_v2/followup_task.rs)：区分向已有 Agent 发送后续任务与新建 Agent。

本次采用这些实现中适合现有系统的简单机制，继续使用已有 Responses 运行时与 DSH 工具。它没有因此获得 Codex 的完整 Agent harness；是否比单个 Codex 更有效，需要在同一任务与资源条件下另做对照，不能从节点数量推断。
