# 事件驱动的并行研究示例

用户希望 4 个研究员 + Leader。此示例是**用户已明确全部配置并批准该协作协议之后**的流程；若只给人数/模型/任务，先按 SKILL.md 询问缺项，不能自行选择下述模式/目录/权限。

示例选择：Owner 创建 5 个 worker，共用一个 group；Codex、指定模型及 high 强度、fork；各自独立 direct 目录（无迁移/Git 初始化）；无 GPU、extra_env={}；不用 doorbell；通过完成通知继续任务。绝对共享目录已由用户指定，不是 daemon 自动分配，五个角色只写各自文件。每个 Agent 的 system_prompt_tpl 写明职责、共享路径、角色名和下述协议；prompt_tpl 明确为 ""，任务通过 prompt 发送。全部创建后才一次性提交任务。保留研究成果，结束停运行/归档 Agent，保留 group 记录。此处的 task_id/version 是任务约定，不是 daemon 新增字段。

## 共享状态

Owner/Leader 在共享目录维护 manifest.json：

```json
{"task_id":"research-2026-01","version":1,"status":"active",
 "assignments":{"R1":"主题 A","R2":"主题 B","R3":"主题 C","R4":"主题 D"},
 "expected":["R1","R2","R3","R4"],"consumed":[]}
```

每个研究员仅写 results/Rn-v1.json，先写临时文件再原子替换，不共同修改 manifest。示例结果：`{task_id,version,assignee,status:"done",summary,evidence_paths}`。Leader 唯一写 manifest 和最终报告。fork 回合必须先读当前共享记录；不能依赖上次会话上下文。

Owner 一次创建角色并记录真实 ID，随后给每个研究员发一次任务（R2–R4 同理）：

```bash
hamlet call message_to --stdin <<'JSON'
{"target":"R1","group":"g-实际ID","prompt":"task research-2026-01 v1，研究主题 A。先读约定共享目录 manifest，仅处理当前有效版本。把进度写自己的状态文件；完成写 results/R1-v1.json 后只给 Leader 发一次 DONE 通知；不要发 RUNNING/ACK/自我提醒。真正阻塞时发一次 BLOCKED 并写原因。","dedup_key":"research-2026-01-v1-R1-submit","dedup_window":"7d"}
JSON
```

Owner 结束当前回合。创建/提交成功无需广播“已提交”“开始了”等新执行消息。

## 研究员完成一次后通知

写入结果成功后调用：

```bash
"$HAMLET_NODE" "$HAMLET_CLI" call message_to --stdin <<'JSON'
{"target":"Leader","prompt":"DONE research-2026-01 v1 R1；结果见约定共享目录 results/R1-v1.json","dedup_key":"research-2026-01-v1-R1-done","dedup_window":"7d"}
JSON
```

然后结束本回合。真正阻塞用独立 blocked 键并记录需要何种决策；不要用“RUNNING/继续等待/收到”通知。给自己发“90 秒后检查”只会立即排队，可能不断 fork，不能替代定时器。

## Leader 每次被结果唤醒

1. 读取 manifest，若 task 已结束、version 过期或通知已 consumed，直接结束，不回复确认。
2. 集中读取当前版本所有已存在的结果，即使此次只收到一名研究员通知。校验 task_id/version/assignee，记录已处理通知，防止重复提交。
3. 结果不全且无阻塞：保存状态并结束回合，等待其他研究员约定的完成通知；不自发查询消息。
4. 有真正阻塞：只给需要行动的角色分配一次明确任务，或向有决策权的调用者报告。新一轮使用 version=2、新键；旧指令到来先读 manifest 后结束。
5. 四份结果齐备：写报告和 status=complete；按授权约定只发送一次最终结果。若需外部提醒，由已配置脚本/外部宿主提供，不假设存在可寻址的 Owner 会话。

每份完成通知仍各自触发一个回合；后续已入队的通知不能自动合并，Leader 可读到齐备结果后让后续回合无动作结束。目标是少量有用的在途消息，当前 daemon 不保证每 Agent 至多一条 pending。

## doorbell 方案（仅在用户选择时）

如果工作依赖外部异步事件，可按用户明确脚本/条件/计时起点配置 doorbell。脚本读取外部源，变化时用稳定事件键 ring 一次；state 保存已提交位置。不要每 60 秒无条件生成新 ID 并唤醒 Agent。脚本的轮询和 Agent 自发消息链不同：脚本只在发现新事件时排队。

```python
#!/usr/bin/env doorbell-python
import json, os
from pathlib import Path
from doorbell import ring, state, every

@every(seconds=60)
def check():
    path = Path(os.environ['EVENT_FILE'])  # 用户指定的外部事件 JSON 文件
    if not path.exists():
        return
    event = json.loads(path.read_text())
    if event['id'] != state.get('last_event'):
        ring(prompt=f"处理事件 {event['id']}，先读共享记录验证版本",
             dedup_key=f"external-{event['id']}", dedup_window="7d")
        state.set('last_event', event['id'])
```

计时从脚本顶层结束后进入循环，首次检查立即发生，此后每轮完成后间隔 60 秒。若用户要从任务提交起 90 秒提醒一次，持久化 submitted_at/目标时间，在脚本中到点 ring 一次并持久化已提醒标记；没有原生 delayed-message 参数。脚本到点入队到 Agent 真正读取仍可能等待 FIFO/目录锁。

结束任务先写 complete，停止 doorbell（防止新触发），由 Owner 查询/取消过期 queued 消息，按批准策略归档/删除 Agent/group。Worker 可写完成记录并通知，不能假设自己有 Owner 清理权限。不对 LGTM/ACK/完成回执反复回复，从而形成循环。
