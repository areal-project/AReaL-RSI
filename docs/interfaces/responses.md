# Responses 会话运行时

`modules/responses.py` 负责一次模型回合、工具调用和会话持久化。Agent 职责、消息投递、任务推进、GPU 作业和共享经验由上层模块管理。

```python
from pathlib import Path
from areal_rsi.modules.responses import (
    ResponsesConfig, ResponsesRuntime, ToolCall, ToolResult,
)

runtime = ResponsesRuntime(ResponsesConfig(
    base_url=base_url,
    api_key=api_key,  # 从环境变量读取；运行时不归档凭证。
    model="gpt-6-astra",
    reasoning_effort="xhigh",
))

async def handle(call: ToolCall) -> ToolResult:
    # report_store.put_once 必须按 call.call_id 幂等写入。
    report = await report_store.put_once(call.call_id, call.arguments)
    return ToolResult({"report_id": report.id}, stop=True, reason="waiting")

result = await runtime.turn(
    Path("runs/run_1/agents/research_1/sessions/session_1"),
    system_prompt=research_prompt,
    user_message="分析这轮结果，提交 report 后等待下一条指令。",
    message_id="assignment_1",  # 上层持久消息 ID，重投递时保持不变。
    tools=tool_definitions,     # Responses function tool JSON Schema。
    handler=handle,
    recovery_state={"assignment_id": "assignment_1", "active_jobs": []},
)
```

## 接口与边界

- `ResponsesConfig` 默认 `gpt-6-astra / xhigh`，支持输出 token、HTTP timeout、每回合模型请求次数、自动 compact 阈值。配置不会隐式切换模型、降低 effort 或重试请求。
- `turn()` 返回 `TurnResult(session_id, turn_id, text, stop_reason, usage)`。`stop_reason` 为 `completed`、`budget_exhausted` 或停止工具提供的原因（默认 `waiting`）。累计 usage 包含 compact 和 reasoning tokens；请求原始 usage 也单独归档。
- `ToolCall(name, arguments, call_id, recovered)` 交给异步 handler，返回 `ToolResult(output, stop=False, reason="waiting")`。output 必须可 JSON 序列化或为字符串。
- `stop=True` 在工具完成后立即结束回合。同一模型响应中尚未执行的工具会收到明确的 skipped 记录，不执行副作用。等待期间没有后台模型调用；调度器只能用新的 user 消息唤醒。
- 完成后的同一个 `message_id` 重复投递直接返回保存的结果，不重复调用模型或工具；不同消息内容不得复用 ID。后续指令在相同目录中继续同一逻辑会话。
- `user_message=None` 恢复被取消、中断或达到预算的回合，不重复添加用户消息。一个会话只允许一个未完成回合；恢复后才能添加下一条新指令。
- 有限的每回合请求次数上限跨进程恢复保持有效；更换有限配置或普通恢复不会自动扩充已保存回合的额度。达到上限后，用户可通过工作台「重试」授予一份新的有限额度；等待状态不消耗请求额度。`max_model_calls_per_turn=None` 表示无本回合次数上限，工作台的 Interaction 使用此设置，其他角色沿用配置。

- `incoming_messages` 回调返回尚未收件的用户消息。runtime 在完整工具序列处理后、下一次模型请求前将其写入 history/context 及当前 turn 的 followups，再发出 message_injected 事件确认收件。相同消息 ID 在恢复时只确认、不重复写入。检查点保留真实补充消息的来源；未完成的工具和已经保存的响应优先恢复，避免重放副作用。
- 取消执行中的 asyncio task 会关闭当前 HTTP 流，不再执行后续工具。第三方服务端是否立即停止推理、是否计费，取决于提供方，运行时不会宣称远端已停止。

## 归档与恢复

会话目录包含 `session.json`、`events.jsonl`、`requests/`、`responses/`。目录由调用方放在所属 run 下。`session.json` 保留完整 history、用于推理的当前 context、回合、工具调用账本、usage 和 compact 检查点。每次工具执行前后都原子保存状态；同一会话的线程与进程并发执行由锁拒绝或串行化。

完成的工具不再执行。若进程在副作用生效后、结果保存前退出，运行时无法单独保证外部系统的 exactly-once：恢复时会用**相同的 call_id** 和 `recovered=True` 调用 handler。handler 必须通过该 ID 查询已经创建的消息、作业或记录并返回原结果；无法判断结果时应报告未决状态，不能盲目重新执行。不同 call_id 的工具仍可具有相同参数，不应按参数粗暴去重。

完整 Responses output 会回放，包括 encrypted reasoning 和 assistant `phase`，不依赖远端 conversation 或 `previous_response_id` 的保留期。`response.incomplete`、协议错误与 HTTP 错误会归档并报错，未完整结束的响应不会执行其中的工具。

显式重试使用 `turn(..., user_message=None, retry_id="持久重试ID")`。Coordinator 只在用户重试失败 Agent 时生成 ID，并绑定当前 active message；engine 恢复时重复传递同一 ID。runtime 在耗尽的回合上为每个新 ID 增加一份 `max_model_calls_per_turn` 额度，保留同一 turn、完整请求计数和已完成工具账本。ID 的消费和新额度先持久化；取消或进程重启不会重复增加。未耗尽的失败回合继续使用剩余额度。新 ID 不重置整个 run 的模型、Token 或时间预算；全局不足时仍会停止。

有一种不同的恢复情况：Research Agent 已输出普通最终回复，却未提交 report。此时模型回合确已完成，调度器将它标为明确的报告协议错误，等待用户重试。重试会结束旧消息投递，并用持久重试 ID 投递一条补报告的 user 消息：保持同一个 session、原任务和原节点，只补齐原任务的报告或明确等待，不授权下一项研究。原始轨迹与工具结果继续保留。普通传输错误或取消仍恢复原回合，不走这个纠正流程。

每次模型请求的 instructions 都带有本回合剩余调用数（包括当前调用），剩余不超过四次时明确要求收束工作，提交有事实依据的 report 或进入明确 wait。这个提示帮助模型安排收尾，硬边界仍由运行时和调度器执行。

## Compact 与事件

默认在新回合开始前检查 context，也在同一长回合的完整工具 batch 结束后、下一次模型请求前检查；context JSON 超过 180,000 个字符时，使用同一模型生成继续工作所需的摘要。字符数是可配置的估算阈值，不是 token 上限，单次超大返回仍可能超过模型限制。压缩前必须清空 pending 并完成每个 function call/output 配对；已有未消费响应先恢复，不能被 compact 越过。原始历史、工具账本和 turn/message/call ID 始终保留，不依赖第三方 native compact API。

compact 请求与后续继续请求都计入当前回合和整个 run 的预算。若剩余额度不足以完成这两次请求，会保留完整边界并停止；不会另起回合或增加额度。检查点保存对应的 history 边界和请求 ID；压缩响应已保存时直接恢复它，检查点已提交时直接继续，避免重启后对同一边界重复 compact。摘要要求保留可恢复的事实、记录引用和未完成步骤，并控制在 12,000 字符以内。

压缩指令只放在该次请求的 instructions 中，不伪装成用户消息。检查点以 assistant 记忆记录提供给后续请求，摘要不能产生新的用户授权、暂停或再次确认要求。检查点中的 `active_instruction` 直接复制当前持久回合的消息 ID、原文和是否已加入上下文，不能由摘要模型改写。每次压缩后的正常请求（含旧检查点恢复）都获得恢复规则及当前原始任务，继续尚未完成的工作；真实用户要求暂停或仅总结时仍须遵守。

恢复 prompt 要求保留目标、硬限制、当前任务、决策理由、具体事实与反例、记录引用、等待中的消息和作业，并区分已测结果与推测。上层的 `recovery_state` 提供当前保存的状态；摘要与派生的 brief 如果和真实用户或负责人的消息矛盾，应核对消息来源，不能把压缩指令写回用户需求。原始 history 和旧检查点保留以便审计。

`event_sink` 接收结构化事件：`model_request`、`response_usage`、`model_stream`、`tool_started`、`tool_completed`、`turn_completed`、`turn_stopped`、`turn_budget_exhausted`、`turn_retry_granted`、`session_compacted`。事件包含 session / turn ID 和时间；模型请求与 usage 带 request ID。上层可据此统计预算、呈现流式文本、更新 Agent 状态。事件订阅者应按 ID 去重；恢复时以归档状态为准。

协议依据：[OpenAI Function calling](https://developers.openai.com/api/docs/guides/function-calling)、[Conversation state](https://developers.openai.com/api/docs/guides/conversation-state)。测试使用可注入 transport 和真实 Responses 数据形状，覆盖等待零请求、连续上下文、取消、工具幂等恢复、compact、预算、SSE 和协议错误。
