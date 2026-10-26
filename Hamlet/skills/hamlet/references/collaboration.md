# Leader / Worker 协作

Leader/Worker 是本例的任务分工名称，不是系统身份；权限仍由 admin/worker 配置。本例使用一个 Leader 和若干 Worker，同组、fork 模式、各有独立目录，以消息通知完成。人数、模型、资源和工作目录按实际任务配置；Worker 的本次分工由 Leader 决定。

## 任务与交接

Leader 负责拆分、派发和验收。每项任务说明目标、输入、输出路径、验收标准、任务版本和真实回复地址；按任务需要安排派发与后续处理。

共享记录至少包含 task_id、version、状态、结果路径，以及以下地址：

| 字段 | 来源与用途 |
| --- | --- |
| logical_id | 创建 Agent 返回的短 ID，用于派发新任务 |
| working_instance_id | 接收者从 whoami / HAMLET_INSTANCE_ID 读取的长 ID，用于继续该任务 |
| reply_to_instance_id | 派发者自己的真实长 ID，用于返回结果 |

例如任务 research-01 v1，Leader 是 `aaaaaa-1`，Worker 的逻辑 ID 是 `bbbbbb`。Leader 用实例 CLI 派发：

```bash
"$HAMLET_NODE" "$HAMLET_CLI" call message_to --stdin <<'JSON'
{"target":"bbbbbb","prompt":"research-01 v1：验证方案 A 的吞吐。输入 /shared/input.json；输出 /shared/results/R1-v1.json，包含测量条件、结果和复现命令。登记 working_instance_id；完成或阻塞时通知 reply_to_instance_id=aaaaaa-1。","dedup_key":"research-01-v1-R1-submit","dedup_window":"7d"}
JSON
```

这是向逻辑节点派发，创建新的 Worker 会话。以下假设 Worker 实际登记为 `bbbbbb-1`，不是预先猜测的序号。Worker 写好结果后通知：

```bash
"$HAMLET_NODE" "$HAMLET_CLI" call message_to --stdin <<'JSON'
{"target":"aaaaaa-1","prompt":"DONE research-01 v1 R1；working_instance_id=bbbbbb-1；结果 /shared/results/R1-v1.json。","dedup_key":"research-01-v1-R1-done","dedup_window":"7d"}
JSON
```

这是向真实实例回复，resume 原 Leader。需修复时，Leader 将修复任务发给 `bbbbbb-1`，附新的请求版本和去重键；独立新任务才发给 `bbbbbb`。目标归档后不能 resume，先核实任务状态，不自动改发逻辑 ID。

## 汇总与失败处理

Worker 各写自己的状态和结果文件，Leader 写汇总 manifest，避免并发覆盖。新 fork 从持久记录恢复任务背景，不继承旧会话。

Leader 被唤醒后检查当前版本的全部结果，而非只处理通知中提到的一份。可结合结果文件和通知判断进展，结果齐备后验收并安排下一步。每条被接受的消息都会触发一轮执行，包括给自己的消息；消息不会插入正在执行的回合。

可恢复的阻塞由 Worker 发送一次 BLOCKED，包含失败步骤、证据和需要的决策。模型调用或进程失败可能让 Worker 无法发送通知；Owner 或预先配置的监控应检查失败 run，查看已有产物后决定恢复。同一任务恢复使用已登记的真实实例 ID，避免重复派发。

消息按 target + dedup_key 在窗口内去重，业务文件和消费记录仍需按任务版本保证重复执行安全。入队回执只确认提交，不能当作结果或等待中的实时 ACK。

## 外部事件：doorbell

需要等待外部作业或事件时，可启用 doorbell。脚本负责检测外部变化并发送消息，触发条件由任务需要决定。

```python
#!/usr/bin/env doorbell-python
import json, os
from pathlib import Path
from doorbell import ring, state, every

@every(seconds=60)
def check():
    path = Path(os.environ['EVENT_FILE'])
    if not path.exists():
        return
    event = json.loads(path.read_text())
    if event['id'] != state.get('last_event'):
        ring(prompt=f"处理事件 {event['id']}，读取共享记录核对任务版本",
             dedup_key=f"external-{event['id']}", dedup_window="7d")
        state.set('last_event', event['id'])
```

本例首次立即检查，此后每轮完成再等 60 秒。需要延迟提醒时，在脚本中保存提交时间、到期时间和已提醒标记；prompt 中写“稍后执行”不会延迟入队。脚本的绑定目标也遵循 fork/resume 规则：逻辑 fork 目标会启动新会话。

收尾先写终态，再停止 doorbell；Owner 查询并取消过期 queued 消息，按约定保留或归档资源。Worker 通知完成即可，不假设自己拥有清理权限。
