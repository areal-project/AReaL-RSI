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
| GET `/api/bootstrap` | `bootstrap` | `{}` → `{allow_simulation, default_simulated, has_config, models: [{id, efforts}], default_model_settings}`；HTTP 层追加 `csrf_token` |
| GET `/api/runs` | `list` | `{}` → `{"runs": [...]}` |
| POST `/api/runs` | `create` | `{message?, message_id?, model_settings?}` → `{run_id}` |
| GET `/api/runs/{id}?after=N&event_limit=100` | `detail` | `{run_id, after, event_limit?}` → 下方快照 |
| POST `/api/runs/{id}/chat` | `chat` | `{run_id, text, message_id?, reply_to?, recipient?}` → `{run_id, message_id, status}` |
| POST `/api/runs/{id}/model-settings` | `model-settings` | `{run_id, base_url, model, reasoning_effort, api_key?}` → `{run_id, status}` |
| POST `/api/runs/{id}/start` | `start` | `{run_id}` → `{status}` |
| POST `/api/runs/{id}/pause` | `pause` | `{run_id}` → `{status}` |
| POST `/api/runs/{id}/resume` | `resume` | `{run_id}` → `{status}` |
| POST `/api/runs/{id}/stop` | `stop` | `{run_id, reason?}` → `{status}` |
| POST `/api/runs/{id}/retry` | `retry` | `{run_id, agent_id}` → `{status}` |

创建请求接受可选首条消息、消息 ID 和 `model_settings`，也可以用 `{}` 新建空对话。模型设置在第一轮对话前保存。HTTP 层固定传入 `conversation=true`，不接受模板选择或模拟模式。Interaction 逐步保存需求与执行条件；用户已明确授权开始时，可用 `handoff_research` 交接给 RSI，否则先发布总结供用户确认。

chat/control 在消息或状态持久化后返回，不等待模型完整回合。Web 不提供通用产物导出入口；候选快照与研究结论按 run 保存。内部应用的旧归档操作不属于 Web API。

模型设置仅在 `status=planned` 且没有 `started_at` 时可写。后端串行处理保存与启动，拒绝开始后的变更；暂停、停止不解除锁定。地址必须是不含凭证、查询参数或片段的 HTTP(S) 地址。模型支持预设和自定义名称；预设按 `models` 目录校验推理强度。`create.model_settings` 与 `model-settings` 接受地址、模型、推理强度及可选 `api_key`；创建时省略设置则继承服务端默认值。空密钥沿用当前值。

公开设置包含 `base_url / model / reasoning_effort / editable / has_credentials`，不返回密钥。普通配置存入 `coordination/config.json`，修订存入 `configurations/`；密钥独立保存在 run 内的 `credentials.json`，权限 `0600`，配置只保存文件引用。`bootstrap.default_model_settings` 提供默认模型设置及密钥是否已配置，不包含密钥值。准备阶段修改配置会中断当前 Interaction 请求，沿用原会话恢复；已经完成的工具不会重做。即使工作线程尚未进入循环，取消时也会释放资源。

已经开始过的研究即使被停止，也会显示「恢复研究」。用户调用 `resume` 后接续原 run、会话、报告和候选；已完成作业不重放，取消的未完成任务由 RSI 重新安排。单独被停止的方向不自动恢复。最新验证为基础设施错误时，概览显示 `execution_blocker: {job_id, error, created_at}`；RSI 应提问并等待处理，不能将环境阻塞作为结束研究的理由。

回复问题时，`reply_to` 使用 `waiting_for_user[].message_id`，后端按原提问者投递，不经过另一个 Agent 转述。只有被回答的问题会移出待回复列表。`recipient` 可为 `interaction` 或 `rsi`；与 `reply_to` 同传时必须匹配。没有 `reply_to` 时，显式 `recipient` 表示独立消息；两者都省略时，若恰有一个待回复问题则自动回复它，否则发给 Interaction，保留所有待回复问题。RSI 仅在研究启动后接收用户消息。

客户端以稳定 `message_id` 重试同一请求；已归档回复的接收者和 `reply_to` 不会因问题已关闭而改变，也不会重复唤醒 Agent。新请求指向不存在、跨 run 或已回答的问题时返回 400。暂停期间可以归档消息，但必须继续研究后才会执行。前端明确显示回复对象，保留输入草稿，不会因轮询发现问题已被其他窗口回答而将草稿自动发给另一个对象。

`detail` 在每次读取时返回完整公共快照；只有 `events` 按 `after` 增量返回：

```text
run_id, objective, status, simulated, started_at?, finished_at?, worker_alive?
brief: { objective, instructions, limits: ResearchLimits, verification: contract|null }
intake: { status: discussing|ready, summary?, environment?, execution_required? }
model_settings: { base_url, model, reasoning_effort, editable, has_credentials }
pending_agents: [agent_id]
plan: string
agents: [{ agent_id, direction?, state, current_iteration_id?, wait_reason?, error? }]
nodes: [{ iteration_id, agent_id, state, parent_iteration_id?, title?,
          assignment: { assignment_id, instruction, completion_criteria?, references },
          report: ResearchReport|null, evidence: [EvidenceRecord],
          references: [{uri}], verification: VerifiedResult|null }]
edges: [{ source: iteration_id, target: iteration_id, kind: parent|reference|merge, label? }]
baseline, best, final: VerifiedResult|null
budget: { model_calls, total_tokens, verify_calls, reserved_tokens?, elapsed_seconds? }
jobs: [{ job_id, agent_id, agent_name, status, title, iteration_id?, candidate_id?,
         verification, is_baseline, phase?, created_at, started_at?, finished_at?,
         gpu_ids, progress?, progress_message?, error?, result? }]
messages: [{ message_id, sender, recipient, text, created_at, reply_to? }]
waiting_for_user: [{ message_id, agent_id, question, created_at }]
events: [{ sequence, kind, created_at, agent_id?, iteration_id?, data }]
event_cursor: 当前最大的事件序号
budget_stop_reason?: string
```

父节点连线可由前端根据 `parent_iteration_id` 推导。额外 `edges` 表达有实际证据的引用或合并关系，不得仅为视觉效果虚构。`messages` 只呈现发给用户或来自用户的消息；Research Agent 给 RSI 的报告在节点详情中展示。分数必须来自正式验证记录；不存在的起点、最佳版本和最终确认返回 `null`，不能补零或复用旧分数。

新报告通过 `report.message` 展示自然语言正文；旧七字段报告仍按原栏目呈现。没有独立完成条件时不展示空栏目。

`/?run=<run_id>` 可直接打开指定研究；切换研究时同步更新地址，便于分享同一份搜索图。

## 执行进度

研究图展示所有 ResearchIteration，默认仅附加未结束的执行作业；展开后可查看全部作业。作业使用 `job:<job_id>` 作为前端选择标识，不创建额外的 ResearchIteration；有 `iteration_id` 时连接其所属研究节点。历史记录中 RSI 直接提交的作业显示在「RSI 历史作业」一行；当前角色约定由研究员承担实际执行。节点、作业和当前进展卡都可打开对应详情，「定位进行中」在未完成任务之间切换。

`rsi` 是提交者 ID，界面显示为 RSI Agent；`search` 是迭代验证阶段，不是联网搜索。起点候选的 `search` 验证显示「起点评测」，其他候选显示「迭代验证」，`final` 显示「最终验证」，`diagnostic` 显示「诊断检查」。起点仍然可为空。

执行器可在配置传入的 `{output}/progress.json` 原子写入以下公共元数据，无须调用模型：

```json
{
  "stage": "逐项验证",
  "message": "2 项已结束，6 项仍在执行。",
  "completed": 2,
  "total": 8,
  "unit": "项",
  "updated_at": "2026-09-09T10:00:00Z",
  "items": [
    {"id": "case-1", "label": "子任务 1", "status": "running", "message": "正在调试"}
  ]
}
```

字段均可省略。`completed` 表示执行结束的数量，可以包含失败项；它与通过数、正式分数无关。只有合法的 completed/total 才显示进度条。`updated_at` 使用带时区的 ISO 时间；执行中的进度超过 45 秒未更新会标出时间，不能据此认定作业停止。没有进度文件时显示真实生命周期和耗时，不推算百分比或剩余时间。

文件最大 128 KB，最多展示 100 个分项，文本字段有长度上限。解析失败、半写入、超限或符号链接均忽略，不中断页面刷新。公共接口只保留规定字段，执行器不得在这些字段中写入凭证或原始日志。进度文件不参与评分、候选选择或调度决策；正式结果仍由可信验证器产生。

「当前进展」汇总运行中的作业、Agent 最近的工具活动、待回复问题和调度连接状态。正在运行的研究节点展示当前任务；Agent 休眠等待作业时明确显示「等待作业结果」，不显示成仍在调用模型。

## 安全与交互

所有写入请求需要 `GET /api/bootstrap` 返回的 `csrf_token`，放入 `X-RSI-Token` 请求头。服务器验证 Host、Origin、Sec-Fetch-Site，拒绝跨站请求和预检，不使用跨域许可。JSON 请求上限为 128 KiB。静态文件为显式白名单；API 不提供任意路径下载。返回快照再次按公共字段投影，排除 config、原始 session、内部对话和私有验证材料。

准备阶段显示对话和需求摘要，研究开始后展示计划、搜索图和执行记录。没有验证协议时隐藏成绩卡。`agents[].direction` 是 RSI 为当前方向起的名称，不由前端填写固定场景标题。配置、环境变量和密钥不进入 intake 公共投影。

消息接收对象和手机端研究切换共用 `dropdown.js` 的 `WorkbenchDropdown`，以 `update(choices, value, disabled)` 更新选项、当前值与禁用状态。按钮和弹出菜单均使用工作台样式；支持键盘选择、文字定位、Esc/外部点击关闭、长列表滚动和视口内定位。菜单渲染在卡片之外；新建研究弹窗内的菜单挂载在弹窗中，保持可交互。后台刷新保留当前浏览位置。

浏览器每 1.5 秒获取公共状态，不会触发模型。事件游标断开后继续获取，节点选择和图的滚动位置按 run 保存在浏览器；刷新不会变成新的模型任务。对话输入和节点详情独立更新，后台刷新不会重置正在输入的消息或当前详情滚动位置。模型和作业状态由 backend 提供。正式 ResearchApp 默认过滤模拟记录并拒绝访问其详情；原始数据的来源标记不被改写。

验收 fixture：`.venv/bin/python tests/test_workbench.py --serve`，在 `http://127.0.0.1:8877` 展示模拟研究和创建/聊天/开始/控制交互。fixture 只用于界面验收；真实应用需连接 Coordinator 和 ResearchEngine。HTTP 测试覆盖这些操作、事件游标、空分数/零分数、字段投影和跨站防护。

## 加载与耗时

`event_limit` 可选，范围 1–1000。工作台请求最近 100 个增量事件；后端通过事件序号直接跳过旧文件。省略参数仍返回完整增量事件，兼容其他消费者。`event_cursor` 用于下一次请求，限制窗口不删除磁盘事件。前端按 run 保留快照与游标，切换时取消过时请求，并拒绝把其他研究的迟到响应写进当前页面。

研究历时取 `started_at → finished_at`，未结束时取当前时间；包括等待和暂停。节点历时取 `created_at → report.created_at`，无报告的结束节点取 `updated_at`；研究已结束时不继续累加残留节点的时间。界面不据此推算模型计算时长或剩余时间。

前端 DOM 交互回归：`npm ci --prefix tests/frontend && npm test --prefix tests/frontend`。覆盖切换竞态、节点耗时、缩放、作业过滤、消息重试与新建配置。它不替代真实浏览器的视觉验收。
