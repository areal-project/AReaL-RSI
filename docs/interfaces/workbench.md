# 本地研究工作台

`ui/workbench.py` 提供 HTTP 和静态界面，`WorkbenchBackend` 提供应用操作。HTTP 层不直接读取 run、原始会话、环境配置或私有验证文件。

```python
from areal_rsi.ui.workbench import WorkbenchServer

server = WorkbenchServer(backend, port=8766)
server.serve_forever()
```

服务器固定绑定 `127.0.0.1`。默认创建独立 asyncio 后台线程，通过 `run_coroutine_threadsafe` 调用异步 backend；已有应用事件循环可以用 `loop=` 注入。`serve_forever` 放在主线程或另一个线程，不能阻塞传入的 asyncio 循环。关闭服务器会取消其自建循环中的剩余任务；外部循环的生命周期由调用者管理。

## Backend 接口

实现 `async call(action: str, params: dict) -> dict | WorkbenchDownload`。需要向用户解释的输入错误抛 `ValueError`，记录不存在抛 `FileNotFoundError`，其他异常只返回通用错误。不要把凭证或原始配置放入异常消息。

| HTTP | action | params / 返回值 |
| --- | --- | --- |
| GET `/api/bootstrap` | `bootstrap` | `{}` → `{allow_simulation, default_simulated, has_config}`；HTTP 层追加 `csrf_token` |
| GET `/api/runs` | `list` | `{}` → `{"runs": [...]}` |
| POST `/api/runs` | `create` | `{template, objective, instructions, max_agents, simulated}` → `{run_id}` |
| GET `/api/runs/{id}?after=N` | `detail` | `{run_id, after}` → 下方快照 |
| POST `/api/runs/{id}/chat` | `chat` | `{run_id, text, message_id}` → `{run_id, message_id?}` |
| POST `/api/runs/{id}/start` | `start` | `{run_id}` → `{status}` |
| POST `/api/runs/{id}/pause` | `pause` | `{run_id}` → `{status}` |
| POST `/api/runs/{id}/resume` | `resume` | `{run_id}` → `{status}` |
| POST `/api/runs/{id}/stop` | `stop` | `{run_id, reason?}` → `{status}` |
| POST `/api/runs/{id}/retry` | `retry` | `{run_id, agent_id}` → `{status}` |
| POST `/api/runs/{id}/export` | `export` | `{run_id, candidate_id?}` → `WorkbenchDownload` |

`template` 为 `kernelbench`、`cuhkx` 或 `research`，只用于创建时选择起步配置。业务工作流仍由 RSI 规划。chat/start/control 应在持久写入消息或控制状态后返回，不要等待模型完整回合。空 `candidate_id` 表示导出当前最佳产物，backend 应根据候选 ID 构建归档并返回 `WorkbenchDownload(path, filename, media_type="application/zip")`；HTTP 层流式下载，宿主路径不会返回前端。

`detail` 在每次读取时返回完整公共快照；只有 `events` 按 `after` 增量返回：

```text
run_id, objective, status, simulated, started_at?, finished_at?, worker_alive?
brief: { objective, instructions, limits: ResearchLimits, verification: contract|null }
plan: string
agents: [{ agent_id, direction?, state, current_iteration_id?, wait_reason?, error? }]
nodes: [{ iteration_id, agent_id, state, parent_iteration_id?, title?,
          assignment: { assignment_id, instruction, completion_criteria, references },
          report: ResearchReport|null, evidence: [EvidenceRecord],
          references: [{uri}], verification: VerifiedResult|null }]
edges: [{ source: iteration_id, target: iteration_id, kind: parent|reference|merge, label? }]
baseline, best, final: VerifiedResult|null
budget: { model_calls, total_tokens, verify_calls, reserved_tokens?, elapsed_seconds? }
jobs: [{ job_id, agent_id, status, title?, description?, gpu_ids?, progress_message?, error? }]
messages: [{ message_id, sender, recipient, text, created_at }]
waiting_for_user: [问题文本，或包含 text/question 的对象]
events: [{ sequence, kind, created_at, agent_id?, iteration_id?, data }]
event_cursor: 当前最大的事件序号
budget_stop_reason?: string
```

父节点连线可由前端根据 `parent_iteration_id` 推导。额外 `edges` 表达有实际证据的引用或合并关系，不得仅为视觉效果虚构。`messages` 只呈现发给用户或来自用户的消息；Research Agent 给 RSI 的报告在节点详情中展示。分数必须来自正式验证记录；不存在的起点、最佳版本和最终确认返回 `null`，不能补零或复用旧分数。

## 安全与交互

所有写入请求需要 `GET /api/bootstrap` 返回的 `csrf_token`，放入 `X-RSI-Token` 请求头。服务器验证 Host、Origin、Sec-Fetch-Site，拒绝跨站请求和预检，不使用跨域许可。JSON 请求上限为 128 KiB。静态文件为显式白名单；API 不提供任意路径下载。返回快照再次按公共字段投影，排除 config、原始 session、内部对话和私有验证材料。

创建弹窗根据 bootstrap 的能力字段设置模拟开关。真实配置默认不选模拟；未启用模拟时禁止勾选；既无配置也不允许模拟时，说明启动方法并禁用创建。开放式研究选项需要已有执行配置。

浏览器每 1.5 秒获取公共状态，不会触发模型。事件游标断开后继续获取，节点选择和图的滚动位置按 run 保存在浏览器；刷新不会变成新的模型任务。对话输入和节点详情独立更新，后台刷新不会重置正在输入的消息或当前详情滚动位置。模型和 GPU 作业的真实状态由 backend 提供，模拟 run 始终显示明确标记。

验收 fixture：`.venv/bin/python tests/test_workbench.py --serve`，在 `http://127.0.0.1:8877` 展示模拟研究和完整创建/聊天/开始/控制/下载交互。fixture 只用于界面验收；真实应用需连接 Coordinator 和 ResearchEngine。HTTP 测试覆盖这些操作、事件游标、空分数/零分数、字段投影和跨站防护。
