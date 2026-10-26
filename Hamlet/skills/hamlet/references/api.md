# CLI / HTTP API

## 服务连接

构建、安装及服务管理见仓库 README。以下调用以连接已选定、认证已配置为前提。下文的 hamlet 可替换为 `bash "$hamlet_cli"`，hamlet_cli 按主 skill 定位 scripts/hamlet。

- Owner 连接已有服务：成对设置 HAMLET_URL/HAMLET_TOKEN。凭据来自已配置环境或授权数据目录；从本机目录读取时核对 daemon.json 的 URL，令牌仅在进程内使用。
- Owner 启动本机服务：在授权的 HAMLET_HOME 执行 `hamlet start`。同目录已有健康服务时会复用；实际 URL 以返回值为准。`status` 不启动服务，`call`/`ring`/`ui` 在未绑定连接时可能自动启动。
- 后台 Agent：直接使用注入的 HAMLET_URL/HAMLET_TOKEN、HAMLET_NODE/HAMLET_CLI 和 HAMLET_SKILL_DIR，不重新选择服务或读取本机 Owner secret。

使用本 skill 的 `scripts/hamlet` 入口；`skill path` 查询源 skill，`skill install --path /absolute/target` 安装完整目录，目标已存在时拒绝覆盖。Owner 安装副本需随源码更新；后台 hamlet-agent skill 由服务安装到工作目录。

本机状态目录由 HAMLET_HOME 指定，默认 ~/.hamlet。daemon.json 保存 URL/PID，0600 secret 用于派生 Owner 凭据。服务绑定 127.0.0.1，实际端口读取启动结果。

## 调用格式与错误

```bash
hamlet call whoami '{}'
hamlet call agent_create --file /absolute/task/agent.json
hamlet call message_to --stdin <<'JSON'
{"target":"abcdef","prompt":"task R1 v1: 研究主题 A，结果写入约定文件，完成只通知一次","dedup_key":"R1-v1-submit","dedup_window":"7d"}
JSON
hamlet ring abcdef --prompt '已批准的首次任务' --dedup-key R1-v1-submit --dedup-window 7d
```

后台 Agent 使用 `"$HAMLET_NODE" "$HAMLET_CLI"`。JSON 文件/stdin 避免 shell 引号/多行模板损坏。call 成功在 stdout 输出 JSON；失败 stderr 输出 `{error,details?}`，退出码 1。不要把自然语言拼接成 shell 代码。

HTTP `POST /api/tools/<name>`，body 是同一 JSON 对象；`POST /api/ring` 和 CLI ring 对应；头部为 `Authorization: Bearer <token>`、`Content-Type: application/json`。返回 JSON，HTTP 非 2xx 时 `{error,details?}`。常见 400 参数/模板错误、401 认证失败、403 无权限、404 不存在/跨坊隐藏、409 迁移/状态冲突。needs_migration 的 details 有 root/entries/count，读取摘要后按用户已授权策略处理；未授权不重试 migrate=true。网络失败且提交结果未知时，使用同一个 dedup_key 重试，不能换键重复提交。

子 Agent 的 HTTP 等价调用（不输出请求头）：

```python
import json, os, urllib.request
req = urllib.request.Request(
    os.environ['HAMLET_URL'] + '/api/tools/whoami', data=b'{}',
    headers={'Authorization': 'Bearer ' + os.environ['HAMLET_TOKEN'],
             'Content-Type': 'application/json'}, method='POST')
with urllib.request.urlopen(req, timeout=30) as r:
    print(json.dumps(json.load(r), ensure_ascii=False))
```

`GET /health` 无认证，只返回进程健康；`GET /api/identity` 返回 whoami。`GET /api/events` 是认证 SSE（ready/change/agent）；agent 事件按身份过滤，change 只是状态变化提醒，不能当作原生任务终态回调。`POST /api/shutdown` 仅 Owner。Host/Origin 校验只允许本机地址及显式配置的公开地址。UI 的 ui_open 返回临时登录 URL，含凭据，不放共享记录；UI 会换成 HttpOnly/SameSite=Strict cookie 后清除查询参数。

## 创建请求

以下为独立 direct 目录的示例；替换 group、模型、目录和任务内容。选项语义及 prompt 覆盖规则见 [配置](configuration.md)。

```json
{
  "group":"g-xxxx", "role_name":"Research1", "identity":"worker",
  "harness":"codex", "model":"用户指定的模型",
  "harness_options":{"model_reasoning_effort":"high"},
  "run_mode":"fork", "workdir":"/absolute/research/r1", "worktree":false,
  "prompt_tpl":"处理任务 {{task}}；版本 {{version}}",
  "system_prompt_tpl":"完成分配的研究任务，按验收标准提交证据与结果；完成或阻塞时通知任务中指定的真实回复实例。",
  "extra_env":{}, "doorbell_script":"", "doorbell_enabled":false
}
```

direct 不传 git_init/base_ref/migrate；worktree 模式按目录现状设置，迁移已有内容需授权。空模板用 ""，沿用 CLI 模型用 null。GPU、duration、callback、start 不是创建参数。

agent_create 返回 `{id,group_id,role_name,identity,harness,model,harness_options,extra_env,run_mode,workdir,execution,...}`；workdir 是对象，包含 id/root/mode 和适用的 main_path/shared_path/base_ref。execution 解释实际并发限制。resident_instance_id 初始为 null，首次触发才分配。agent_list/group_agents 返回逻辑 Agent 数组及其 instances；instance_get 返回 config_snapshot、状态、会话、pending_messages 和 runs（含结果）。

## 工具清单

下表列出 CLI/HTTP 真实参数；`?` 表示可选。参数名用下划线。所有实例调用限本坊。Admin 管理只限本坊 worker，不能创建/提升为 admin 或管理其他 admin。

| 工具 | 参数 | 结果/权限 |
| --- | --- | --- |
| whoami | {} | id/role_name/group/identity/shared_dir；Owner 或实例 |
| group_create / group_list | {name} / {} | group 对象/数组；Owner |
| group_update / group_delete | {id,name?,paused?} / {id} | group/ok；Owner；暂停保留队列 |
| agent_create | 上述创建配置 | 逻辑 Agent；Owner/admin |
| agent_get / agent_list | {id} / {group?} | 对象/数组；本坊可读 |
| group_agents | {} | 本坊 Agent 数组，实例使用 |
| agent_update | {id,role_name?,identity?,harness?,model?,harness_options?,extra_env?,prompt_tpl?,system_prompt_tpl?,run_mode?,workdir?,worktree?,git_init?,migrate?,base_ref?,doorbell_script?,doorbell_enabled?} | 更新逻辑配置；Owner/admin；目录相关选项只在传 workdir 时使用，已有目录元数据不改写 |
| agent_delete | {id} | {ok:true}；Owner/admin，停止/归档/软删除 |
| instance_get | {id} | 实例与运行；本坊可读 |
| instance_archive | {id} | ok；Owner/admin；停止并清理实例目录 |
| instance_renew_resident | {logical_id} | ok；Owner/admin，下次触发创建新实例 |
| instance_fork | {logical_id,prompt?,vars?} | 入队消息；Owner，强制新实例 |
| run_stop | {instance_id} | ok；Owner/admin，终止当前运行 |
| message_to | {target,group?,prompt?,system_prompt?,vars?,dedup_key?,dedup_window?} | 入队消息；短 ID=fork 模式逻辑节点新建实例，长 ID=resume 指定实例；resident 逻辑节点按常驻规则处理；Owner/本坊实例 |
| message_force_push | {id} | 原拒绝消息入队；Owner 或原发送实例，仅 rejected 状态可用 |
| message_list | {group?,logical_id?,instance_id?,target?,source?,from_id?,status?,since?,until?,limit?,offset?,order?} | 消息数组；Owner 或本组实例 |
| message_get | {id} | 消息和所有运行尝试；Owner |
| message_retry / message_cancel | {id} | ok；Owner；失败/已取消可重试，仅 queued 可取消 |
| run_list | {group?,logical_id?,instance_id?,message_id?,source?,from_id?,status?,since?,until?,limit?,offset?,order?} | run 数组；Owner |
| run_get | {id} | 单次运行；Owner |
| run_events | {id,limit?,offset?,order?} | 事件数组；Owner/本坊实例 |
| doorbell_control | {logical_id,action:"start"/"stop"/"restart"} | ok；Owner/admin |
| doorbell_logs | {logical_id} | {state,log}；Owner/admin |
| doorbell_script_get / doorbell_script_save | {logical_id} / {logical_id,script} | {script}/ok；Owner/admin；save 会立即启用并重启 |
| instance_diff / instance_merge | {id} / {id,files?,confirm_overwrite?} | diff/合并结果；Owner；Git 冲突留给人工解决，non-Git 覆盖需明确授权 |
| workdir_refresh | {id:工作目录ID} | ok；Owner，刷新 non-Git base |
| branch_delete | {id:已归档实例ID,force?} | ok；Owner，仅 Git |
| state | {} | 全局 groups/agents/messages/doorbells/pid；Owner |
| ui_open | {} | 带认证的 URL；Owner，勿记录到共享状态 |

`/api/ring` 与 message_to 参数一致；CLI ring 用 `--group`、`--prompt`、`--system-prompt`、重复 `--var k=v`、`--dedup-key`、`--dedup-window`。target 可为逻辑短 ID、实例长 ID、角色名或角色名#序号；Owner 用角色名时传 group 避免歧义。

### message_to

签名：`{target,group?,prompt?,system_prompt?,vars?,dedup_key?,dedup_window?}`。**没有 mode 参数**，目标决定路由：

| target | 路由 |
| --- | --- |
| 逻辑短 ID，例如 abcdef，run_mode=fork | fork 新实例 |
| 真实长 ID，例如 abcdef-1，任意 run_mode | resume 指定实例 |
| 逻辑短 ID，run_mode=resident | 首次创建，之后复用常驻实例 |

角色名解析为逻辑目标，角色名#序号解析为指定实例；任务交接优先使用已记录的 ID。忙碌实例的消息排在后续回合，已归档实例不能 resume。具体派发和回复见 [协作示例](collaboration.md)。

回执中的 next_action / planned_instance_id 表示计划动作，非完成结果。去重命中或消息已派发时，这些字段可能缺省；保留消息 id，按需查询 message_get 或实例状态。

### 积压拒绝与强制推送

积压按目标真实实例统计；resident 的逻辑 ID 与常驻实例共用队列（首次创建前也如此）。fork 逻辑 ID 和 instance_fork 创建新会话，不汇总其他实例的积压。提交前该队列已有 > 5 条 queued 消息时，保存 rejected 记录并返回 HTTP 409；已有 5 条仍接受第六条。dispatched、rejected 和终态消息不计入。检查和写入在同一同步操作内完成。

CLI 错误退出码为 1，stderr 与 HTTP 错误正文均为 `{error,details}`。error 说明“目标 Agent 已有 X 条排队消息，本次消息已拒绝。是否确认继续推送？”，details 包含：

```json
{
  "code":"target_queue_overloaded", "message_id":"被拒绝消息的 ID",
  "target_id":"解析后的目标 ID", "queued_count":6, "threshold":5,
  "status":"rejected",
  "confirmation":{"tool":"message_force_push","params":{"id":"被拒绝消息的 ID"}}
}
```

调用方确认继续时执行 `message_force_push {"id":"拒绝 ID"}`。服务只将原消息加入队尾，保留发送者、目标及内容；拒绝数量和强推者/时间保存在 rejection 中。未知 ID 返回 404；非 rejected（包括已推送）返回 409；其他发送实例调用返回 403，跨组目标仍不可见。Owner 可强推所有拒绝消息，doorbell 凭据不能调用该工具。目标已删除或实例已归档时仍拒绝推送。

同一发送者的相同 target/dedup_key 在窗口内命中 rejected 消息时，再次返回同一拒绝 ID。队列缩短或 daemon 重启不会自动接纳 rejected 消息；它不能通过 message_retry 入队，可以用 message_cancel 放弃。message_retry 同样检查目标队列积压，超过阈值时将原消息改为 rejected 并返回其 ID。强推并发或重复调用只有第一次成功，之后报错。

## 消息、运行与去重

message 状态 queued/dispatched/done/failed/cancelled/rejected；run 状态 running/succeeded/failed/stopped/interrupted。message_to 返回 id/status/queue_reason/next_action/sender 等；`queue_reason.code` 为 workdir_busy、instance_busy、group_paused、fifo、awaiting_dispatch 或 target_unavailable，可能带阻塞 run/实例。它是当前快照，不能承诺起跑时间。

message_get 的 runs 保留重试历史；run_get 的 prompt 是实际投递内容，final_output/usage/error 为结果。触发来源 host/ui/ring/message_to/doorbell_script；Owner 来源不证明用户亲自输入。trigger_is_snapshot=false 时不能推断未保存的触发数据。since/until 是 ISO 时间，消息按 created_at、运行按 started_at；order=asc/desc。显式 limit 1–500、offset≥0；HTTP/CLI 不传分页时部分接口返回全量，建议明确分页。查询不会启动模型回合。

去重范围是 from_id + target_id + dedup_key，在 dedup_window（如 24h、7d）内返回旧消息并标 deduplicated=true；不同实例/逻辑目标不是同一范围，不同发送者的键空间相互独立。缺 key 不去重。不要用相同键表示修改后的任务；新 version 用新键。重试/崩溃不提供 exactly-once；业务结果写入和消费仍需任务版本/已处理记录。

daemon 重启把正在运行的 run 标 interrupted、对应消息 failed，不自动重放；queued 继续调度。先查询失败记录和输出，再决定是否重试，不能把入队响应当结果。
