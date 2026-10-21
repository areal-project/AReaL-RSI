# CLI / HTTP API

## 安装、发现、启动

从仓库构建：`corepack pnpm install --frozen-lockfile && corepack pnpm build`。需要 Node 20+、Git、Python 3.9+，以及已安装/登录的 codex 或 claude。Skill 位于 `skills/hamlet`，完整目录可复制到所用宿主的 skills 目录；安装命令见仓库 README。子 Agent 直接读取 daemon 提供的源 Skill 路径，不依赖宿主安装或 MCP 工具发现。

```bash
node /absolute/Hamlet/bin/hamlet.js --help
node /absolute/Hamlet/bin/hamlet.js skill path
node /absolute/Hamlet/bin/hamlet.js status  # 不启动 daemon
node /absolute/Hamlet/bin/hamlet.js start   # 明确授权启动后
```

Skill 的连接前提是：**用户提供或明确选定现有服务 URL 与凭据来源，或明确授权 Owner 现场启动。** 没有这两者之一时先询问，不能用业务调用顺便启动服务。文中的 `call`、`ring`、`ui` 示例均以服务已选定、连接已绑定为前提。

连接现有服务时，通过环境成对提供 `HAMLET_URL` 与 `HAMLET_TOKEN`，或向选定 URL 发认证 HTTP 请求。凭据可来自用户已配置的环境，或用户授权使用的本机数据目录；使用后者时，先核对该目录 `daemon.json` 中的 URL 与用户选定的服务一致，在进程内读取和使用凭据，不打印密钥或令牌。不要把令牌字面量写入命令参数、Skill、prompt 或共享记录。指定连接认证失败或不可达时停止并报告，不清除变量后改连本机 Owner，也不另起服务。

用户授权本机启动后，在获授权的数据目录（`HAMLET_HOME`，或用户允许的默认目录）执行 `start`；返回的 `url` 才是本次实际服务地址，告知用户并用于后续调用。`start` 是“发现或启动”：同一目录已有健康 daemon 时会复用；如需全新服务，先明确独立数据目录。服务重启后重新读取地址，不能把示例端口、上次地址或安装目录当作当前连接配置。只有获授权的本机连接才可读取该目录的 `daemon.json` 解析或核对地址；发现文件不构成使用服务或启动服务的许可。

本地状态目录默认 ~/.hamlet（仅有旧 ~/.detached 时复用旧目录），HAMLET_HOME 指定目录；daemon.json 含 url/pid。daemon 只监听 127.0.0.1 随机端口。Owner CLI 从该目录的 0600 secret 派生凭据，不需显示 token。显式 HAMLET_URL/HAMLET_TOKEN 成对设置时，CLI 连接该服务；子 Agent 已由 daemon 设置实例身份，不能读取 secret 或删除这些变量回退 Owner。DETACHED_* 是兼容别名，HAMLET_* 优先。daemon 保留身份变量，extra_env 中的 HAMLET/DETACHED URL/TOKEN/INSTANCE_ID/RUN_ID 和 DOORBELL_* 不会覆盖子 Agent 的身份。DOORBELL_URL/TOKEN 只能 ring 自己，不可查询工具。

CLI `call` 自动发现或启动服务；`status` 仅查健康，不证明凭据有效，用 `call whoami '{}'` 验证权限。`start` 返回 `{url,status:"running"}`；`status` 返回健康 JSON 或 `{status:"stopped"}`。`stop` 仅 Owner，影响全部坊；服务安装/卸载用 `install-service`/`uninstall-service`（Linux/macOS）。

## 调用格式与错误

```bash
hamlet call whoami '{}'
hamlet call agent_create --file /absolute/task/agent.json
hamlet call message_to --stdin <<'JSON'
{"target":"abcdef","prompt":"task R1 v1: 研究主题 A，结果写入约定文件，完成只通知一次","dedup_key":"R1-v1-submit","dedup_window":"7d"}
JSON
hamlet ring abcdef --prompt '已批准的首次任务' --dedup-key R1-v1-submit --dedup-window 7d
```

未将 bin 加入 PATH 时以 `node /absolute/Hamlet/bin/hamlet.js` 替代 hamlet。子 Agent 使用 `"$HAMLET_NODE" "$HAMLET_CLI"`。JSON 文件/stdin 避免 shell 引号/多行模板损坏。call 成功在 stdout 输出 JSON；失败 stderr 输出 `{error,details?}`，退出码 1。不要把自然语言拼接成 shell 代码。

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

`GET /health` 无认证，只返回进程健康；`GET /api/identity` 返回 whoami。`GET /api/events` 是认证 SSE（ready/change/agent）；agent 事件按身份过滤，change 只是状态变化提醒，不能当作原生任务终态回调。`POST /api/shutdown` 仅 Owner。跨源及非本机 Host 拒绝。UI 的 ui_open 返回临时登录 URL，含凭据，不放共享记录；UI 会换成 HttpOnly/SameSite=Strict cookie 后清除查询参数。

## 创建请求

以下是**全部配置已由用户选定后**的 direct 示例，不是让调用 Agent自行套用默认值。先替换 group、目录、模型和职责等为配置表的明确值。CLI/HTTP 当前仍为旧调用方保留默认值，Skill 必须主动把适用值传全。

```json
{
  "group":"g-xxxx", "role_name":"Research1", "identity":"worker",
  "harness":"codex", "model":"用户明确指定的模型",
  "harness_options":{"model_reasoning_effort":"high"},
  "run_mode":"fork", "workdir":"/absolute/research/r1", "worktree":false,
  "prompt_tpl":"处理任务 {{task}}；版本 {{version}}",
  "system_prompt_tpl":"研究主题 A。先读共享记录，仅写自己的结果；完成后按约定通知 Leader 一次。",
  "extra_env":{}, "doorbell_script":"", "doorbell_enabled":false
}
```

git_init/base_ref/migrate 已在配置表记为 direct 不适用，故不传。worktree 模式需明确 git_init、migrate；Git 模式明确 base_ref（用户授权使用 HEAD 时可传 "HEAD"）。model 可以是字符串或用户明确授权 CLI 模型的 null。空模板用 ""；不使用 doorbell 用空脚本串和 false。不能传 duration、GPU、start、callback 等不存在的字段。

agent_create 返回 `{id,group_id,role_name,identity,harness,model,harness_options,extra_env,run_mode,workdir,execution,...}`；workdir 是对象，包含 id/root/mode 和适用的 main_path/shared_path/base_ref。execution 解释实际并发限制。resident_instance_id 初始为 null，首次触发才分配。agent_list/group_agents 返回逻辑 Agent 数组及其 instances；instance_get 返回 config_snapshot、状态、会话、pending_messages 和 runs（含结果）。

## 工具清单

下表列出 CLI/HTTP 真实参数；`?` 表示协议可选，不代表 Skill 可以静默依赖默认。参数名用下划线。所有实例调用限本坊。Admin 管理只限本坊 worker，不能创建/提升为 admin 或管理其他 admin。

| 工具 | 参数 | 结果/权限 |
| --- | --- | --- |
| whoami | {} | id/role_name/group/identity/shared_dir；Owner 或实例 |
| group_create / group_list | {name} / {} | group 对象/数组；Owner |
| group_update / group_delete | {id,name?,paused?} / {id} | group/ok；Owner；暂停保留队列 |
| agent_create | 上述全部适用配置 | 逻辑 Agent；Owner/admin |
| agent_get / agent_list | {id} / {group?} | 对象/数组；本坊可读 |
| group_agents | {} | 本坊 Agent 数组，实例使用 |
| agent_update | {id,role_name?,identity?,harness?,model?,harness_options?,extra_env?,prompt_tpl?,system_prompt_tpl?,run_mode?,workdir?,worktree?,git_init?,migrate?,base_ref?,doorbell_script?,doorbell_enabled?} | 更新逻辑配置；Owner/admin；目录相关选项只在传 workdir 时使用，已有目录元数据不改写 |
| agent_delete | {id} | {ok:true}；Owner/admin，停止/归档/软删除 |
| instance_get | {id} | 实例与运行；本坊可读 |
| instance_archive | {id} | ok；Owner/admin；停止并清理实例目录 |
| instance_renew_resident | {logical_id} | ok；Owner/admin，下次触发创建新实例 |
| instance_fork | {logical_id,prompt?,vars?} | 入队消息；Owner，强制新实例 |
| run_stop | {instance_id} | ok；Owner/admin，终止当前运行 |
| message_to | {target,group?,prompt?,system_prompt?,vars?,dedup_key?,dedup_window?} | 入队消息；Owner/本坊实例 |
| message_list | {group?,logical_id?,instance_id?,target?,source?,from_id?,status?,since?,until?,limit?,offset?,order?} | 消息数组；Owner |
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

## 消息、运行与去重

message 状态 queued/dispatched/done/failed/cancelled；run 状态 running/succeeded/failed/stopped/interrupted。message_to 返回 id/status/queue_reason/next_action/sender 等；`queue_reason.code` 为 workdir_busy、instance_busy、group_paused、fifo、awaiting_dispatch 或 target_unavailable，可能带阻塞 run/实例。它是当前快照，不能承诺起跑时间。

message_get 的 runs 保留重试历史；run_get 的 prompt 是实际投递内容，final_output/usage/error 为结果。触发来源 host/ui/ring/message_to/doorbell_script；Owner 来源不证明用户亲自输入。旧记录 trigger_is_snapshot=false，不能推断未保存的触发数据。since/until 是 ISO 时间，消息按 created_at、运行按 started_at；order=asc/desc。显式 limit 1–500、offset≥0；HTTP/CLI 不传分页时部分接口返回全量，建议明确分页。查询不会启动模型回合。

去重范围是 target_id + dedup_key，在 dedup_window（如 24h、7d）内返回旧消息并标 deduplicated=true；不同实例/逻辑目标不是同一范围，不同发送者共用目标也共享键空间。缺 key 不去重。不要用相同键表示修改后的任务；新 version 用新键。重试/崩溃不提供 exactly-once；业务结果写入和消费仍需任务版本/已处理记录。

daemon 重启把正在运行的 run 标 interrupted、对应消息 failed，不自动重放；queued 继续调度。先查询失败记录和输出，再决定是否重试，不能把入队响应当结果。
