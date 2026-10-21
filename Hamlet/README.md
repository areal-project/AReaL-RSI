# Hamlet · 坊

> Hamlet — a quiet village of detached agents, woken by a doorbell.

**一座 Agent 各自做事、有事敲门的小村落。**

Hamlet 是英语里的“小村落”。中文名“坊”取自唐代长安的里坊：城中街区各有坊墙，彼此独立；“坊”也有作坊的意思，是做事的地方。

在 Hamlet 里，每个 **group 是一座坊**。坊内的 Agent 各自独立干活，有事敲门（`doorbell`）唤醒，彼此串门（`message_to`）协作，但不越过坊墙。坊墙对应工具权限与通信边界，文件系统与工作目录由使用者安排。

Hamlet 通过 **Skill + CLI/HTTP** 为宿主提供单机后台 Agent 执行能力，宿主与子 Agent 都无需配置 MCP。独立 daemon 负责持久化消息、拉起运行、维护权限与记录；宿主会话结束，坊里的工作仍可继续。当前支持 Codex 与 Claude Code，并附带 CLI、Web UI 和 Python doorbell 库。

## 几个简单约定

- **各自做事**：每个实例有自己的会话，可选独立 worktree。`fork` 每次新建实例，`resident` 保留上下文。
- **敲门才醒**：所有触发先进入持久化消息队列。`message_to` 异步返回，每条消息会触发一个执行回合。
- **坊内串门**：Worker 与 Administrator 只在本坊查询和通信；Owner 可以管理和观察各坊。
- **看得见等待**：同一实例按 FIFO 执行，共用 direct 目录全局串行；接口和 UI 明确说明排队原因。
- **自行组织**：任务拆分、交接、版本、验收与截止控制由 Agent、调用方和 doorbell 约定。

## 安装与启动

需要 Node.js 20+、Git、Python 3.9+。使用 Codex 时需安装并登录 `codex`；Claude 适配器需安装并登录 `claude`。构建无需模型凭据。

```bash
git clone git@github.com:yulangz/Hamlet.git
cd Hamlet
corepack enable
corepack pnpm install --frozen-lockfile
corepack pnpm build
node bin/hamlet.js start
node bin/hamlet.js ui --open
```

全新安装的状态目录为 `~/.hamlet/`。升级时若仅有旧 `~/.detached/`，会继续复用该目录。设置 `HAMLET_HOME=/absolute/path` 可使用独立数据目录。daemon 只监听 `127.0.0.1`，端口自动选择并写入 `daemon.json`。`start` 和 CLI `call` 都会自动发现或启动 daemon；PID 锁保证只启动一份。

可把仓库的 `bin/` 和 `packages/py-doorbell/bin/` 加入 `PATH`，之后直接使用 `hamlet`、`doorbell ring` 和 `doorbell-python`。下面示例使用仓库内的入口，不依赖全局安装。

```bash
node bin/hamlet.js status
node bin/hamlet.js stop
node bin/hamlet.js install-service    # 可选：systemd 用户服务或 launchd
node bin/hamlet.js uninstall-service
```

## 安装 Hamlet Skill

统一入口是 [skills/hamlet/SKILL.md](skills/hamlet/SKILL.md)，按需引用配置语义、API 与事件驱动协作示例。把完整 `skills/hamlet` 目录装入所用宿主的 skills 目录，例如 Codex：

```bash
node bin/hamlet.js skill path
node bin/hamlet.js skill install --path "${CODEX_HOME:-$HOME/.codex}/skills/hamlet"
# Claude Code 使用独立的目标目录：
node bin/hamlet.js skill install --path "$HOME/.claude/skills/hamlet"
```

目标必须是明确的绝对路径；已存在时命令拒绝覆盖，更新前自行检查本地修改。重新加载宿主的 skills 后，可直接要求使用 Hamlet 或显式调用 `$hamlet`。也可在宿主会话中直接读取仓库中的 SKILL.md。构建及安装 Skill 不启动 daemon，不需注册 MCP server。

**先选服务，再创建 Agent。** Skill 不内置服务地址：用户提供或明确选定现有服务 URL 和凭据来源，或明确授权 Owner 现场启动；后者以 `start` 返回的实际 URL 为准。连接已有服务时绑定 URL/凭据，失败就报告，不通过 CLI 的自动启动另起服务。启动授权、动态端口和本机发现规则见 [Skill](skills/hamlet/SKILL.md#先确定服务与启动授权)。

子 Agent 每回合收到 Skill 路径与 CLI 使用说明；harness 通过环境提供实例 URL/令牌、CLI 和 Skill 路径，身份不写入 prompt 或 MCP 配置。子 Agent 使用 `"$HAMLET_NODE" "$HAMLET_CLI" call whoami '{}'` 确认自己，随后通过 CLI/HTTP 工作。daemon 每次重新判定权限；实例凭据不完整时 CLI 报错，不能静默回退本机 Owner。

**创建前必须明确每项配置。** 调用 Agent 从当前请求及已有明确授权提取配置；没有明确值、明确不适用或用户授权默认的参数，先解释影响并合并询问，收到答案前不创建 group/Agent、不初始化/迁移目录、不启动服务或脚本。尤其不能自行选择 fork/resident、worktree、模型、权限或目录。完整配置已明确后直接执行，不增加重复确认。详见 [配置清单](skills/hamlet/references/configuration.md)。HTTP/CLI 为兼容旧客户端仍保留服务端默认值，这不表示 Skill 可以静默使用它们。

## 建一座坊，开始一次运行

以下仅演示**用户已授权完整配置**后的请求：一个 worker，Codex 指定模型/high 强度、resident、指定独立 direct 目录、无 GPU/额外环境、空模板、不开 doorbell、创建后发送一次任务；git_init/base_ref/migrate 因 direct 而明确不适用。实际请求必须使用用户的值，缺项先询问。

```bash
node bin/hamlet.js call group_create '{"name":"用户指定的开发坊"}'
# 将实际 group ID、用户指定的目录和模型填入文件；JSON 文件支持多行模板
node bin/hamlet.js call agent_create --stdin <<'JSON'
{
  "group":"g-实际ID", "role_name":"实现者", "identity":"worker",
  "harness":"codex", "model":"用户指定的模型",
  "harness_options":{"model_reasoning_effort":"high"}, "run_mode":"resident",
  "workdir":"/absolute/user-selected/project", "worktree":false,
  "prompt_tpl":"", "system_prompt_tpl":"实现用户授权的任务，结果写入约定记录。",
  "extra_env":{}, "doorbell_script":"", "doorbell_enabled":false
}
JSON
# 使用返回的逻辑短 ID；入队不会等待结果
node bin/hamlet.js call message_to '{"target":"abcdef","prompt":"实现已授权功能","dedup_key":"task-1-v1","dedup_window":"7d"}'
node bin/hamlet.js call instance_get '{"id":"abcdef-1"}'
```

等价请求为 `POST /api/tools/agent_create` 等，JSON 参数相同、Bearer 认证；也可 `call <工具> --file /absolute/request.json`。完整参数、返回值及生命周期工具见 [CLI/HTTP API](skills/hamlet/references/api.md)。`hamlet ring` / `doorbell ring` 是消息入队的便捷入口，也支持 `--group`、`--system-prompt`、`--var k=v`、`--dedup-key` 和 `--dedup-window`。

`message_to` 不是定时器，prompt 写“90 秒后检查”也会立即入队。正常协作为“提交任务 → 结束回合等待完成事件 → 唤醒后读结果”。进度、RUNNING 和 ACK 写共享记录；仅新结果、真正阻塞及待决策事项发送一次带 task/version 的通知。不要自发消息轮询或相互回复确认。结束不自动回调发送者，需要约定 worker 通知或 doorbell。去重、版本和收尾示例见 [并行研究](skills/hamlet/references/collaboration.md)。当前未实现 pending 合并、批量消费、原生延迟和终态回调。

创建/查询 Agent 时返回的 `execution` 会说明实际并发约束；UI 的“配置 → 执行语义”也会显示共用 direct 目录的其他 Agent。**入队成功不表示已经运行。** `message_to` 和 ring 的返回值中，`queue_reason` 解释当前为什么等待，`next_action` 指明预期 fork/resume；这些都是读取当时的状态，之后可用 `message_get` 更新。

| `queue_reason.code`  | 含义                                                      |
| -------------------- | --------------------------------------------------------- |
| `workdir_busy`       | 同一 direct 目录被其他回合占用；含路径、阻塞实例与 run ID |
| `instance_busy`      | 同一实例的上一回合还在执行；含阻塞 run ID                 |
| `group_paused`       | Group 暂停，保留消息而不调度                              |
| `fifo`               | 同一执行队列还有更早的消息；含前序消息 ID                 |
| `awaiting_dispatch`  | 可以调度，正在等待调度器启动回合                          |
| `target_unavailable` | 目标失效；调度器处理后会在消息上记录失败                  |

消息一旦 dispatched 或结束，`queue_reason` 为 null。查询不会推动队列或唤醒 Agent。共用 direct 目录的 Agent 不应在本轮等待对方 ACK，因为对方必须等当前回合结束才有机会运行。

逻辑 Agent 创建后不会运行。fork 模式每条逻辑目标消息新建实例；resident 模式首次触发才创建常驻实例，后续消息按 FIFO resume。长 ID 始终表示续跑指定实例。不同实例并行执行；相同真实路径的 direct 工作目录全局串行。失败不阻塞后续消息，结束不自动通知发送者。

## Owner 查询与复盘

CLI/HTTP 提供以下 Owner 查询工具；Worker/Administrator 使用本坊查询接口，不能因此获得跨坊查询权限。

| 工具              | 用途                                                                                                           |
| ----------------- | -------------------------------------------------------------------------------------------------------------- |
| `message_list`    | 按 group、logical_id、instance_id、target、source、from_id、status、since/until 筛选消息，并查看当前排队原因   |
| `message_get(id)` | 单条消息及其所有运行尝试，包括失败后重试的旧运行                                                               |
| `run_list`        | 按 group、logical_id、instance_id、message_id、source、from_id、status、since/until 查询历史、结果、用量和耗时 |
| `run_get(id)`     | 查询一次运行的实际 prompt、结果、触发快照和 `queue_wait_ms`                                                    |
| `run_events(id)`  | 分页读取标准化事件，含命令、工具、警告和错误                                                                   |

列表仍返回数组。建议查询显式传 `limit=100`，最大 500；使用 `offset` 翻页，`order=asc/desc` 按持久化顺序排列。消息的时间筛选针对 `created_at`，运行的时间筛选针对 `started_at`。既有 HTTP `message_list` 在不传分页参数时继续返回完整列表；`run_events` 的无分页 HTTP 形式也保留，供 UI 读取完整历史。

```bash
node bin/hamlet.js call message_list '{"group":"g-xxxx","status":"queued","limit":100}'
node bin/hamlet.js call message_get '{"id":"消息ID"}'
node bin/hamlet.js call run_list '{"group":"g-xxxx","order":"desc","limit":20}'
node bin/hamlet.js call run_get '{"id":"运行ID"}'
node bin/hamlet.js call run_events '{"id":"运行ID","limit":100,"offset":0}'
```

`sender` 区分认证身份和调用入口：Owner 的 `message_to` 显示为“宿主调用方 (owner)”，UI 显示“Web UI (owner)”，普通 ring 显示“ring 调用方 (owner)”；Agent 消息保留实例角色与长 ID，脚本使用受限令牌时保持 `doorbell_script` / `doorbell: 角色名`。**Owner 凭据不能证明是用户本人手动输入**，也不能凭它推断脚本名称。需要准确标识自动监听或定时触发时，使用该 Agent 的 doorbell。

升级不改写旧消息的 `from_role` 或旧运行的实际 prompt；查询另提供 `sender` 解释已记录的来源。新运行保存 `trigger_snapshot` 和每次尝试的排队时长，重试不覆盖旧 run。旧 run 没有快照时，`trigger_is_snapshot=false` 且 `queue_wait_ms=null`，不能据此声称恢复了当时未保存的数据。Owner 仍可查询软删除 Agent 的运行与事件；消息筛选不会因目标已删除而混入别的 group。

系统只提供运行与通信事实。任务编号、版本、ACK、`complete`、`FINAL_PASS`、截止时间和收尾协议均属于 Agent、调用方或 doorbell 的约定。

## 工作目录

旧 API 的默认值为开启 worktree、空目录初始化 Git；Skill 必须先取得明确选择。选择 worktree 后形成 `main/`、`worktrees/`、`shared/` 和 `.hamlet/workdir.json`。非空目录首次使用会返回 `needs_migration` 和摘要，不移动任何文件；传 `migrate:true` 才把原内容完整移入 `main/`。UI 会展示摘要并请求确认。传 `worktree:false` 则直接使用原目录，不迁移。

- Git 模式从 main 的 HEAD 或 `base_ref` 创建分支 `agent/<实例ID>`；main 未提交修改不会带入，运行事件中有警告。每次运行结束自动提交实例改动。UI 可查看 diff、合并以及归档后删除保留分支；冲突保留在 main 中供手动解决。
- 非 Git 模式用 `base/` 快照和写时复制创建实例。每个实例另存 fork 时的基准（`.hamlet/bases/<实例ID>/`），刷新 base 只影响之后的 fork。UI 可选择文件应用到 main，若 main 也已改动，必须确认覆盖。
- 归档会清理 worktree，Git 分支保留；非 Git 的实例副本和实例基准会删除。harness 的会话文件不清理。

编辑逻辑配置只影响之后的 fork，已有实例的模型、参数、环境和模板均沿用配置快照。常驻实例要应用新配置时，使用“换新常驻实例”。

## 敲门：自定义 doorbell

在 Agent 的 Doorbell 页面编辑并保存脚本，或创建 Agent 时指定可执行脚本的绝对路径。保存会自动重启。daemon 注入 `DOORBELL_AGENT_ID`、`DOORBELL_GROUP_ID`、`DOORBELL_URL`、`DOORBELL_TOKEN`、`DOORBELL_STATE_DIR`，并把自带解释器包装和 CLI 加入 PATH。

```python
#!/usr/bin/env doorbell-python
import json, os
from pathlib import Path
from doorbell import ring, state, every

# EVENT_FILE 在用户明确授权的 extra_env 中指定；文件由外部事件源原子更新
@every(seconds=60)
def check():
    path = Path(os.environ["EVENT_FILE"])
    if not path.exists():
        return
    event = json.loads(path.read_text())  # 例如 {"id":"task-1-v1-done"}
    if event["id"] != state.get("last_id"):
        ring(prompt=f"处理新事件 {event['id']}，先读共享记录确认版本",
             dedup_key=f"event-{event['id']}", dedup_window="7d")
        state.set("last_id", event["id"])
```

`every` 在脚本顶层完成后立即首次检查，此后按间隔循环；仅事件变化时 ring，避免无条件制造新消息。异常会让脚本退出，交由 daemon 指数退避重启。`state` 用文件锁和原子替换持久化 JSON。`ring` 仅用 Python 标准库发送认证 HTTP 请求。提供[轮询模板](packages/py-doorbell/templates/poll.py)与[监听模板](packages/py-doorbell/templates/listen.py)。也可 `python3 -m pip install ./packages/py-doorbell`，在自己的解释器中导入 `doorbell`。

stdout/stderr 写入 `doorbells/<短ID>/output.log`，UI 实时查看。崩溃重启从 1 秒退避到最多 60 秒；10 分钟内第 6 次崩溃标记 failed，人工启动/重启可复位。停 daemon 保留 enabled 状态，下次启动恢复脚本。脚本令牌只能 ring 所属逻辑 Agent 及其实例。

## HTTP 与状态

认证：`Authorization: Bearer <token>`。本机 Owner 凭据由权限为 600 的 `secret` 派生，本机 Owner CLI 自动读取；子 Agent 使用受限实例环境，不读取 Owner secret。UI 使用带随机密钥签发令牌的首次访问 URL，随即换成 HttpOnly / SameSite=Strict cookie 并清除 URL 查询参数。拒绝跨源请求和非本机 Host。身份权限作用于工具层；不做文件系统隔离。

- `POST /api/ring`：`target`、`group?`、`prompt?`、`system_prompt?`、`vars?`、`dedup_key?`、`dedup_window?`。
- `POST /api/tools/<工具名>`：JSON 参数。UI 的扩展操作还包括 `group_update`、`state`、`message_list/retry/cancel`、`run_events`、`doorbell_script_get/save`、`instance_diff/merge`、`workdir_refresh`、`branch_delete`、`instance_fork`。
- `GET /api/events`：SSE `ready`、`change`、`agent` 事件；实例事件按身份过滤。
- `GET /health`：无凭据的进程健康状态；不包含业务数据。
- `POST /api/shutdown`：仅 Owner。

SQLite 使用 WAL。标准化事件在 `runs/<run_id>.jsonl`，原始事件在同目录的 `.raw.jsonl`。重启后运行中的记录标记 interrupted，触发消息标记 failed，不自动重放；queued 消息继续调度。已知遗留子进程在核对运行标记/令牌后终止，再恢复调度。

## 子 Agent 命令环境

Hamlet 提供 Skill、CLI 与 HTTP 入口，不再提供 MCP server，也不探测或迁移旧 MCP 配置。Codex fork/resume 直接生成 CLI 参数；启动模型前使用相同参数中的配置启动本地 app-server，通过 command/exec 核对命令环境中的受限身份和 CLI/Skill 路径。若这些变量被过滤或替换，或检查不可用/超时，就拒绝启动，不回退 Owner、不放宽用户环境策略。探针不调用模型，令牌仅走环境，不写入参数、prompt 或日志。Claude 使用 strict 空 server 集合，子 Agent 都通过 CLI/HTTP 工作。

已有实例继续沿用模型/模板等配置快照，每回合收到 CLI/Skill 提示和环境；如需更新任务配置，按既有规则换新实例。

## 从 Detached 升级

从 0.1.2 起，英文名统一为 **Hamlet**，中文名为 **坊**。新的 CLI 是 `hamlet`，主入口是 Hamlet Skill，工作区包名使用 `@hamlet/*`，Python 发行包名为 `hamlet-doorbell`；Python 导入名仍是 `doorbell`。

- 旧 `detached` 命令和 `bin/detached.js` 保留为别名，指向同一服务。
- `HAMLET_HOME`、`HAMLET_URL`、`HAMLET_TOKEN`、`HAMLET_CODEX_BIN`、`HAMLET_CLAUDE_BIN` 为主要环境变量，对应的 `DETACHED_*` 仍可使用；同时设置时以 `HAMLET_*` 为准。
- 升级不搬动数据库、日志、Git 分支或已有工作目录。旧数据目录 `~/.detached/` 和旧工作目录标记 `.detached/workdir.json` 继续被识别；新工作目录使用 `.hamlet/`。
- 新安装的用户服务名为 `hamlet.service`（Linux）或 `local.hamlet`（macOS）。若曾安装旧用户服务，可先用 `hamlet uninstall-service` 移除旧服务，再用 `hamlet install-service` 安装新名称。

所有命令和宿主应指向同一个数据目录；若显式指定 `HAMLET_HOME`，在 Owner CLI 与 daemon 服务环境中保持一致。`doorbell`、`message_to`、group ID、实例 ID 和 HTTP API 路径保持原有含义。

## 验证

[开发与验证](docs/verification.md)说明测试范围、文件保留规则及平台验证边界。

```bash
corepack pnpm typecheck
corepack pnpm build
corepack pnpm test
corepack pnpm test:python
corepack pnpm exec playwright install --with-deps chromium
corepack pnpm test:ui
# 可选：调用本机已登录的真实 Codex，会消耗模型额度
node scripts/smoke-codex.mjs
```

测试包括调度、权限、配置快照、持久恢复、Git 和非 Git 目录、真实子进程、CLI/HTTP 权限、Python doorbell、HTTP/SSE 与 Chromium 页面操作。常规测试使用受控 harness / CLI fixture，不消耗模型额度。`smoke-codex.mjs` 额外验证真实 Codex 的受限 CLI 身份、fork、上下文 resume。

Claude 作为第二个适配器共用同一 `Harness` 接口，使用 `claude -p --output-format stream-json --verbose --dangerously-skip-permissions`。普通测试验证其事件/参数契约和子进程调度；真实 Claude 运行依赖本机登录与 CLI 的运行限制。

平台代码包含 Linux reflink/systemd、macOS APFS clone/launchd 分支。当前自动验收在 Linux 上执行；macOS 安装与服务需在目标机器执行相同验收。

Codex 实现时已核对本机 `codex exec --help`、`codex exec resume --help` 及 [OpenAI 配置参考](https://developers.openai.com/codex/config-reference)、[非交互执行文档](https://developers.openai.com/codex/noninteractive)。配置值按 TOML 编码，prompt 走 stdin，命令通过无 shell 的参数数组启动。
