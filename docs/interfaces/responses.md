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
- 每回合请求次数上限跨进程恢复保持有效；达到上限后需由上层显式调整预算才能继续。等待状态不消耗请求额度。
- 取消执行中的 asyncio task 会关闭当前 HTTP 流，不再执行后续工具。第三方服务端是否立即停止推理、是否计费，取决于提供方，运行时不会宣称远端已停止。

## 归档与恢复

会话目录包含 `session.json`、`events.jsonl`、`requests/`、`responses/`。目录由调用方放在所属 run 下。`session.json` 保留完整 history、用于推理的当前 context、回合、工具调用账本、usage 和 compact 检查点。每次工具执行前后都原子保存状态；同一会话的线程与进程并发执行由锁拒绝或串行化。

完成的工具不再执行。若进程在副作用生效后、结果保存前退出，运行时无法单独保证外部系统的 exactly-once：恢复时会用**相同的 call_id** 和 `recovered=True` 调用 handler。handler 必须通过该 ID 查询已经创建的消息、作业或记录并返回原结果；无法判断结果时应报告未决状态，不能盲目重新执行。不同 call_id 的工具仍可具有相同参数，不应按参数粗暴去重。

完整 Responses output 会回放，包括 encrypted reasoning 和 assistant `phase`，不依赖远端 conversation 或 `previous_response_id` 的保留期。`response.incomplete`、协议错误与 HTTP 错误会归档并报错，未完整结束的响应不会执行其中的工具。

## Compact 与事件

默认在**新回合开始前**，当 context JSON 超过 180,000 个字符时，使用同一模型生成继续工作所需的摘要。字符数是可配置的估算阈值，不是 token 上限；单个超大回合仍可能到达模型 context 限制。它只在工具调用全部结算后执行，不依赖第三方 native compact API。原始历史和响应始终保留。

恢复 prompt 要求保留目标、硬限制、当前任务、决策理由、具体事实与反例、记录引用、等待中的消息和作业，并区分已测结果与推测。上层的 `recovery_state` 用于提供当前可信状态，防止摘要中的陈旧结论覆盖 run 的真实状态。

`event_sink` 接收结构化事件：`model_request`、`response_usage`、`model_stream`、`tool_started`、`tool_completed`、`turn_completed`、`turn_stopped`、`turn_budget_exhausted`、`session_compacted`。事件包含 session / turn ID 和时间；模型请求与 usage 带 request ID。上层可据此统计预算、呈现流式文本、更新 Agent 状态。事件订阅者应按 ID 去重；恢复时以归档状态为准。

协议依据：[OpenAI Function calling](https://developers.openai.com/api/docs/guides/function-calling)、[Conversation state](https://developers.openai.com/api/docs/guides/conversation-state)。测试使用可注入 transport 和真实 Responses 数据形状，覆盖等待零请求、连续上下文、取消、工具幂等恢复、compact、预算、SSE 和协议错误。
