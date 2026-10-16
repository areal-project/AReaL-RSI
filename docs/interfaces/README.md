# 当前接口与保留契约

当前入口是 `infra.research.ResearchSystem`。API 数据定义不负责启动系统。

| 契约 | 当前用途 |
| --- | --- |
| `api/research.py`：SessionConfig、SessionRecord、SessionEvent | 配置、持久状态和 UI 事件 |
| 同文件：TaskBrief、ResearchLimits、GoalProgress | Interaction 任务、Leader 进展与预算 |
| `api/control.py`：TaskProposal、StartGoal、GoalUpdate | 三个 Agent 控制工具的请求 |
| `api/research.py`：ExperienceRef、LearningRecord | 固定经验版本与总结状态 |
| `api/control.py`：LessonDraft、TerminalInput | 总结输出和终端 JSON 输入边界 |
| `api/control.py`：decode_record、encode_record、record_schema | JSON 边界统一校验/转换 |
| `api/interfaces.py` | 保留未来协议；本版没有对应注册/实现 |
| `api/records.py`、`execution.py` 及其余 research 契约 | 保留未来研究、版本、评测与任务执行约定 |

`ResearchSystem` 的用户控制：

| 方法 | 作用 |
| --- | --- |
| `create(config)` → run_id | 保存新的普通会话 |
| `open(run_id)`、`detail(run_id)` | 连接/恢复原生历史；读取状态副本 |
| `set_model(run_id, model)` | 保存后续回合和总结使用的模型，保留历史与当前执行状态 |
| `set_effort(run_id, effort)` | 保存后续回合和总结使用的推理强度 |
| `list_models(run_id)` | 使用当前 API 连接查询模型列表，不启动模型回合 |
| `send_message(run_id, text, images=...)` | 开始工作或追加当前 turn 的指令 |
| `enter_rsi(run_id, text)` | 沿用当前上下文，进入 Interaction |
| `start(run_id)` | 交接已准备的任务 |
| `pause`、`resume`、`stop`、`chat` | 同一 run 上的生命周期控制 |
| `answer(run_id, request_id, ...)` | 明确批准/拒绝或回答原生交互 |
| `events(run_id)`、`prompts(run_id)` | 订阅事件；读取待答请求 |
| `learn(run_id, feedback="")` | 在继承原权限的独立会话中总结经验；重复触发复用结果 |
| `close()` | 关闭宿主拥有的进程，保存未完成会话并释放占用 |

控制工具只允许当前 thread/turn 和对应角色调用。Leader 的完成报告必须包含验证证据；
Interaction 不能完成 Goal，普通模式不能调用 RSI 控制工具。

保留契约不表示能力已经实现。本版不为未来协议添加占位类、转发器或兼容运行路径。
