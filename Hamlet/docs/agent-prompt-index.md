# Agent prompt 与工具说明索引

当前实现的审阅快照。各节列出原文来源与生效时机；该索引本身不注入 Agent。

## P01 · 角色 system prompt

位置：[packages/core/src/service.ts:1061](../packages/core/src/service.ts#L1061)

时机与作用：Owner 创建时填写 system_prompt_tpl，新实例启动时渲染；消息 system_prompt 可覆盖。resume 保留原会话职责。共享目录存在时追加路径。

````text
<创建时填写的长期职责，或消息提供的 system_prompt>
共享目录: <实际共享目录路径>

没有共享目录时不追加该行。
````

## P02 · 消息来源与任务正文

位置：[packages/core/src/service.ts:1070](../packages/core/src/service.ts#L1070)

时机与作用：每回合。正文使用消息 prompt，省略时渲染 prompt_tpl；来源头提供发送者角色和真实实例 ID，供消息工具回复使用。

````text
[来自 <来源名称><来源身份后缀>]
<本次任务正文>

实例后缀为 (<真实实例 ID>)；Owner 后缀为 (owner)；事件脚本没有实例 ID。
````

## P03 · admin 管理入口提示

位置：[packages/core/src/service.ts:1066](../packages/core/src/service.ts#L1066)

时机与作用：每回合按当前权限决定。仅 admin 在正文后收到这一句；worker 没有附加提示。

````text
需要查询或管理同组 Agent 时，参考 hamlet-agent skill：<实际 skill 路径>/SKILL.md。
````

## S01 · SKILL.md

位置：[skills/hamlet-agent/SKILL.md:1](../skills/hamlet-agent/SKILL.md#L1)

时机与作用：skill 发现或按需读取，说明服务用途和按身份选择管理参考。

````markdown
---
name: hamlet-agent
description: 当需要查询或管理同组 Agent 时使用，提供 Hamlet 的实例身份、CLI 和管理接口说明。普通任务执行不需要加载管理操作。
---

# 同组 Agent 管理

Hamlet 是当前多 Agent 任务的会话与消息管理服务。admin 可以管理同组 worker；worker 通常不需要与该服务交互，专注完成分配的任务。

admin 需要查询或管理同组 Agent 时，阅读 [管理操作](references/admin.md)，其中包含 CLI 入口、凭据使用方式和接口参考。当前身份可通过该文档中的 whoami 查询。

消息的路由、回复、排队与拒绝确认规则见相应工具描述。
````

## S02 · admin.md

位置：[skills/hamlet-agent/references/admin.md:1](../skills/hamlet-agent/references/admin.md#L1)

时机与作用：admin 需要查询或管理组员时读取；CLI 入口、实例身份和凭据说明集中在这里。

````markdown
# Admin 管理操作

admin 可以在本组查询和通信，并管理本组 worker。运行时提供当前实例身份与连接，CLI 调用方式：

```bash
"$HAMLET_NODE" "$HAMLET_CLI" call whoami '{}'
"$HAMLET_NODE" "$HAMLET_CLI" call group_agents '{}'
```

whoami.id / HAMLET_INSTANCE_ID 是当前真实实例长 ID，group_agents 返回组员的逻辑 ID 与实例列表。凭据通过 HAMLET_URL/HAMLET_TOKEN 使用，不打印凭据、不读取 Owner secret 或替换身份重试。认证失败时报告实际错误。

- 查询、创建、更新或停止本组 worker：按需阅读 [接口](api.md)。
- 设置模型、模板、工作目录和 doorbell：按需阅读 [配置](configuration.md)。

这些权限限定 Hamlet API 操作，不构成文件系统沙箱。
````

## S03 · api.md

位置：[skills/hamlet-agent/references/api.md:1](../skills/hamlet-agent/references/api.md#L1)

时机与作用：admin 需要具体接口参数时按需读取。

````markdown
# Admin 接口参考

统一格式：`"$HAMLET_NODE" "$HAMLET_CLI" call 工具名 'JSON'`。多行内容使用 `--stdin` 或 `--file`。HTTP 等价入口为 HAMLET_URL 下的 POST /api/tools/工具名，使用环境中的 HAMLET_TOKEN 作 Bearer 认证。

| 工具 | 参数 | 用途 |
| --- | --- | --- |
| whoami | {} | 实例 ID、角色、group、权限、共享目录 |
| group_agents | {} | 本组逻辑 Agent 和实例 |
| agent_get / agent_list | {id} / {} | 查询组员配置 |
| instance_get | {id} | 查询本组实例、pending_messages、runs |
| run_events | {id,limit?,offset?,order?} | 查看本组运行日志，limit 为 1–500 |
| message_to | {target,prompt?,system_prompt?,vars?,dedup_key?,dedup_window?} | 提交消息，可能因目标积压而拒绝 |
| message_list | {from_id?,target?,status?,limit?,offset?,order?} | 查询本组消息，包括 rejected 记录 |
| message_force_push | {id} | 确认推送自己发送的 rejected 消息 |

message_to 的 prompt 替代目标实例默认任务模板；省略时渲染 prompt_tpl。system_prompt 仅在新会话替代 system_prompt_tpl，resume 会忽略。直接传入的文本不做模板替换，vars 只用于模板。dedup_window 支持 24h、7d 等时长。去重按发送者、目标和键隔离。角色名解析为逻辑节点，角色名#序号解析为真实实例；使用已记录的 ID 可明确路由。

成功返回 JSON；CLI 失败退出码 1，stderr 为 {error,details}，HTTP 使用对应状态码。积压拒绝返回 409：details 包含 code=target_queue_overloaded、message_id、target_id、queued_count、threshold=5、status=rejected 和 confirmation 调用参数。强推成功返回原消息，其状态变为 queued；重复强推返回 409，未知 ID 返回 404，强推其他发送者的消息返回 403（跨组目标不可见）。

Admin 还可管理本组 worker：agent_create、agent_update、agent_delete、instance_archive、instance_renew_resident、run_stop、doorbell_control。涉及创建或更改配置时，从 `$HAMLET_SKILL_DIR/references/configuration.md` 阅读选项；Owner 专用的 group 管理和全局消息重试不向后台实例开放。
````

## S04 · configuration.md

位置：[skills/hamlet-agent/references/configuration.md:1](../skills/hamlet-agent/references/configuration.md#L1)

时机与作用：admin 创建或更新配置时按需读取。

````markdown
# 配置与 prompt

## 创建配置

从当前任务和已有配置确定下列选项。将实际选择写入创建请求，便于复查；只读查询无需配置 Agent。

| 选项 | 含义与选择依据 |
| --- | --- |
| group、role_name | group 是通信和管理边界；role_name 描述用户指定的任务分工，不决定系统权限 |
| identity | worker 在本组查询和通信；admin 可管理本组 worker。协调任务本身不要求 admin |
| harness、model | codex 或 claude；保留用户指定模型。model:null 沿用该 CLI 的模型配置 |
| harness_options | Codex 选项转为 `-c key=TOML`，如 model_reasoning_effort；Claude 当前读取 effort；无覆盖时用 `{}` |
| run_mode | fork 的逻辑触发创建独立会话；resident 的逻辑触发创建或复用常驻会话 |
| workdir、worktree | 绝对路径；direct 使用原目录，worktree 由 Hamlet 创建实例目录，详见下文 |
| system_prompt_tpl、prompt_tpl | 长期职责与默认任务；可为空串，详见下文 |
| extra_env、资源 | extra_env 值须为字符串，无附加环境时 `{}`；资源限额由任务约定或外部调度器执行 |
| doorbell_script、doorbell_enabled | 外部事件脚本的绝对路径及启用状态；不用时 `""`、false |

Agent 由消息触发执行。启用 doorbell 会启动事件脚本，脚本也可发送消息；创建方案应明确是否启用该触发方式。

## prompt 的作用与覆盖规则

| 配置入口 | 内容 | 生效时机 |
| --- | --- | --- |
| agent_create / agent_update 的 system_prompt_tpl | 职责、约束、交付与协作规则 | 新实例创建时 |
| agent_create / agent_update 的 prompt_tpl | 未显式提供任务时使用的默认任务模板 | 每次消息触发 |
| message_to / ring 的 prompt | 本次具体任务，替代 prompt_tpl | 本次回合 |
| message_to / ring 的 system_prompt | 替代 system_prompt_tpl | 仅新实例；resume 时忽略并记录警告 |
| message_to / ring 的 vars | 模板变量值 | 本次模板渲染 |

省略 prompt/system_prompt 才使用相应模板；显式 `""` 会覆盖模板。**只有模板执行 `{{变量}}` 替换，消息中直接提供的 prompt/system_prompt 按原文使用。** 未知模板变量会在创建实例和目录前报错。

内置变量优先于同名 vars：`agent_id` 是真实实例长 ID；`role` 是角色名；`group` 是 group ID；`from` / `from_role` 是发送者 ID / 标签；`worktree` 是实际执行目录；`shared_dir` 是共享目录或空串；`now` 是当前时间。其余变量来自 vars。变量中不要放凭据。

每个实例保留创建时的配置快照。更新逻辑 Agent 的模型、模板、参数和环境只影响新实例；resume 使用原快照，包括原 prompt_tpl。补充当前任务用消息 prompt；需要新长期配置时创建新实例。`instance_renew_resident` 会归档旧常驻实例，下一次逻辑触发才创建新会话。

Hamlet 每回合发送来源头与任务正文；当前身份为 admin 时，附加一句按需查阅 Agent skill 的提示。角色职责在创建配置中填写，路由与队列规则由工具定义提供。新实例的 system prompt 还会附上共享目录路径（如有）。Codex 将其作为 developer_instructions，Claude 使用 append-system-prompt。实际内容可在 run_get 的 prompt/system_prompt 或运行记录中查看。

## 工作目录与并发

- `worktree:false`：直接使用 workdir，不移动文件、不自动提交；git_init/base_ref/migrate 不适用。同一真实目录的运行全局串行，跨 group 也一样。已由外部创建的独立 Git worktree 也可用此模式接入。
- `worktree:true`：管理 root 下的 main/、shared/、worktrees/。新空目录用 git_init:true 初始化 Git，false 使用文件快照。首次遇非空目录且 migrate:false 时返回 409 needs_migration；migrate:true 会将原内容迁入 main/，须有迁移授权。
- Git 实例从 main 的 HEAD 或 base_ref 创建分支 `agent/<实例ID>`；未提交内容不进入新实例，每回合结束会自动提交。已有 Git 仓库迁移后沿用 Git；git_init 只控制空目录初始化。
- 非 Git 实例复制 base/；workdir_refresh 更新后续实例的基准，已有实例保留原基准。base_ref 只适用于 Git。
- 复用目录时沿用已登记的模式和 base_ref，不能靠重传参数重新初始化。不同实例可并发，但同一实例始终串行；独立目录仍需约定外部共享文件的写入责任。

归档实例会停止运行：Git 删除 worktree、保留分支；非 Git 删除实例副本及基准；direct 保留原目录。模型 CLI 的会话文件不会清理。删除逻辑 Agent 会停止运行、归档实例并取消其 queued 消息；删除 group 对组内全部 Agent 执行删除。

## 环境、资源与 doorbell

daemon 注入实例身份和 CLI/skill 路径，extra_env 不能覆盖受保护的身份变量。Codex 启动前校验实际命令环境；若环境过滤移除了这些变量或校验失败，修正具体原因，不通过更换 Owner 身份或放宽整个环境策略绕过。

Hamlet 不调度 GPU 或强制执行资源配额。按任务约定使用外部调度器或环境配置，并明确分配、共享与释放规则。

Doorbell 脚本在外部事件发生时 ring；其 DOORBELL_URL/TOKEN 只能触发绑定目标。脚本需定义事件条件、稳定去重键和计时起点。Python every 首次立即检查，之后每轮完成再等待；从任务提交或截止时间计时需自行持久化时间。入队时间不等于模型执行时间。

脚本崩溃按 1–60 秒退避，10 分钟内第六次崩溃进入 failed，start/restart 可复位。daemon 重启保留 enabled 状态并恢复脚本。
````

## S05 · role-prompts.md

位置：[skills/hamlet/references/role-prompts.md:1](../skills/hamlet/references/role-prompts.md#L1)

时机与作用：Owner 创建方案阶段填写，用于形成各角色的 system_prompt_tpl 和具体任务；不会原样自动注入。

````markdown
# 角色与任务模板

创建前由 Owner 根据用户目标填写角色 prompt，并将职责草稿纳入创建方案。方括号是填写位置，提交配置前替换或删除；不是模板引擎变量。协调者、执行者是业务分工，系统权限仍单独选择 admin/worker。

## system_prompt_tpl：长期职责

协调任务时可从以下内容开始：

```text
你负责[项目或任务目标]的任务分解、协作安排和结果验收。
根据[输入与现状]确定各成员本次分工，明确输入、输出、完成标准和依赖关系。
依据[评测或验收标准]检查结果，形成[最终交付物]。
需要跨回合保留的任务状态记录在[共享记录路径]。
资源与修改范围：[本任务实际约束]。
```

执行任务时可从以下内容开始：

```text
你负责[职责范围]，目标是[预期结果]。
根据本次任务提供的输入开展工作，交付[产物形式]和[验证证据]。
发现输入缺失、目标冲突或影响结果的限制时，说明具体情况。
协作要求：[结果交接对象与触发条件；没有此需求则删除]。
资源与修改范围：[本任务实际约束]。
```

具体工作方向可由协调者在每次消息中分配，无需在创建时固定。除任务确有需要外，不加入轮询、通知频率、命名格式等通用限制。操作服务的方法在 Agent skill 中，消息行为在工具定义中。

## prompt：当前任务

```text
本次目标：[要完成什么]。
输入：[文件、任务版本、当前结果或依赖]。
交付：[输出位置与内容]。
验收：[如何判断完成]。
其他要求：[本次独有的约束，按需填写]。
```

一次性任务放在消息 prompt 中；稳定的默认任务才放入 prompt_tpl。直接传入的消息文本按原文使用。若使用配置模板的变量，遵循 [配置参考](configuration.md) 的渲染规则。

创建配置不会建立模型会话。新实例使用当时的角色配置；已有实例保留原会话。修改逻辑 Agent 的 system_prompt_tpl 后，新的 fork 才使用新职责；现有会话的任务调整通过消息说明。
````

## T01 · message_to

位置：[packages/agent-tools/src/definitions.ts:4](../packages/agent-tools/src/definitions.ts#L4)

时机与作用：fork/resume 注册，客户端发现工具时提供。路由、回复、去重、排队和拒绝确认说明集中在工具定义。

````json
{
  "name": "message_to",
  "description": "向同组 Agent 发送消息，不接受 mode 参数。fork 模式的逻辑短 ID 创建新会话；真实实例长 ID resume 指定会话；resident 逻辑 ID 创建或复用常驻会话。每条接受的消息触发一轮执行，包括给自己发送；不会插入正在运行的回合。成功回执只表示入队。目标会话已有超过 5 条 queued 消息时，保存 rejected 记录并返回拒绝原因、queued_count 和 message_id；根据任务需要确认继续时使用 message_force_push。被拒绝消息不会自动入队。任务完成后服务不会自动回复发送者。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "target": {
        "type": "string",
        "minLength": 1,
        "description": "目标逻辑短 ID 或真实实例长 ID。同一任务的追问和结果使用已记录的 working_instance_id 或 reply_to_instance_id；未指定回复目标时使用本消息来源中的真实实例 ID。不要猜测实例 ID。来自 Owner 或事件脚本的消息没有默认实例回复地址。"
      },
      "prompt": {
        "type": "string",
        "description": "本次任务或回复正文；省略时使用目标的默认任务模板。"
      },
      "system_prompt": {
        "type": "string",
        "description": "仅创建新会话时覆盖 system prompt；resume 时忽略。"
      },
      "vars": {
        "type": "object",
        "additionalProperties": true,
        "description": "目标模板的变量值；prompt 和 system_prompt 中直接提供的文本不做模板替换。"
      },
      "dedup_key": {
        "type": "string",
        "description": "可选去重键；窗口内同一发送者、目标和键复用原消息。原消息被拒绝时返回相同拒绝 ID；已失败或取消的消息不会因去重重发而执行。"
      },
      "dedup_window": {
        "type": "string",
        "description": "去重窗口，例如 24h、7d；默认 24h。"
      }
    },
    "required": [
      "target"
    ],
    "additionalProperties": false
  }
}
````

## T02 · message_force_push

位置：[packages/agent-tools/src/definitions.ts:45](../packages/agent-tools/src/definitions.ts#L45)

时机与作用：fork/resume 注册，客户端发现工具时提供。路由、回复、去重、排队和拒绝确认说明集中在工具定义。

````json
{
  "name": "message_force_push",
  "description": "确认将自己发送、因积压被拒绝的消息加入目标队尾。使用 message_to 错误中的 message_id，保留原目标和内容。仅接受仍为 rejected 的消息；未知、属于其他发送者或已推送的 ID 会报错。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "id": {
        "type": "string",
        "minLength": 1,
        "description": "message_to 拒绝回执中的 message_id。"
      }
    },
    "required": [
      "id"
    ],
    "additionalProperties": false
  }
}
````

## R01 · execution.description

位置：[packages/core/src/observe.ts:104](../packages/core/src/observe.ts#L104)

时机与作用：agent_get、agent_list、group_agents、instance_get 返回的执行数据说明，也用于 Web UI 展示。用于解释目录并发关系，不作为每回合任务指令注入。

````text
共用此 direct 工作目录的实例串行执行。

不同实例可并行；同一实例串行执行。
````

## R02 · 排队原因

位置：[packages/core/src/service.ts:924](../packages/core/src/service.ts#L924)

时机与作用：消息回执或查询 queued 消息时，返回当前阻塞原因。

````text
Group 调度已暂停；消息保留在队列
等待旧常驻实例结束并完成归档
该实例正在执行上一回合；当前消息按 FIFO 等待，不会实时送入运行中的会话
同一 direct 工作目录全局串行；等待当前回合结束并释放目录
等待同一执行队列中更早的消息被调度
消息已入队，等待调度器启动下一回合；入队成功不表示已经运行
````

## R03 · 积压拒绝

位置：[packages/core/src/service.ts:265](../packages/core/src/service.ts#L265)

时机与作用：新提交或重试超过积压阈值时，保存 rejected 并返回错误及 details.message_id。

````text
目标 Agent 已有 <X> 条排队消息，本次消息已拒绝。是否确认继续推送？
````

## R04 · 运行警告

位置：[packages/core/src/service.ts:1159](../packages/core/src/service.ts#L1159)

时机与作用：相应情形发生时写入日志；读取 run_events 后可见。

````text
resume 无法更换 system prompt，已忽略
main 有未提交改动，本次从 HEAD 创建 worktree
````

## I01 · 接入位置

| 来源 | 作用时机 |
| --- | --- |
| `packages/core/src/service.ts` | 每回合渲染来源与任务，附加 admin 提示；新实例渲染角色 system prompt。 |
| `packages/core/src/skills.ts` | 创建工作目录及每回合准备时安装/更新 skill 文件；不将参考文档全文拼入 prompt。 |
| `packages/harness-codex/src/index.ts` | 新会话将 system prompt 作为 developer_instructions；任务正文经 stdin 提供，fork/resume 均注册工具。 |
| `packages/harness-claude/src/index.ts` | 新会话使用 --append-system-prompt；任务正文经 stdin 提供，fork/resume 均注册工具。 |
| `packages/agent-tools/src/index.ts` | 提供两个工具及其调用结果；无额外 server instructions 或提示模板。 |

其他错误与状态回执由具体操作返回，消息状态机见 [message-lifecycle.md](message-lifecycle.md)。实际投递内容以 run_get 的 prompt/system_prompt 为准。框架文案更新不会改写既有业务模板或历史会话。
