---
name: hamlet
description: 当用户要求用 Hamlet 创建或管理后台 Agent、组织多 Agent 协作、派发任务、查看运行或处理积压消息时使用。供代表用户管理服务的宿主助手（Owner）通过 CLI/HTTP 操作；后台工作实例使用自动安装的 hamlet-agent skill。
---

# Hamlet：Owner 使用指南

Hamlet 管理独立的后台 Codex/Claude 会话。**Owner 是代表用户操作 Hamlet 的宿主助手，或通过 CLI/Web UI 使用 Owner 凭据的管理调用方**。用户在当前对话让你创建和管理 Agent 时，你承担 Owner 的职责。

group 是 Agent 的通信与管理边界；逻辑 Agent 保存配置，具有短 ID；实例是实际工作的模型会话，具有长 ID。Owner 管理所有 group。Hamlet 系统内的 Agent 身份只有 admin 和 worker：admin 可管理本组 worker，worker 可在本组查询和通信。Leader、研究员等其他角色是用户指定的任务分工，不决定系统权限。

## 入口与连接

**使用本 skill 目录中的 `scripts/hamlet`。** 安装器会把实际 Node 和 Hamlet CLI 路径写入该脚本，无需猜测源码位置。如果 skill 安装在项目的 `.agents/skills/hamlet`，在项目根目录执行：

```bash
hamlet_cli=".agents/skills/hamlet/scripts/hamlet"
bash "$hamlet_cli" --help
```

若 skill 位于其他目录，用你读取本 SKILL.md 时的真实目录定位同目录下的 scripts/hamlet。直接读取源码中的 skill 时也使用它自带的脚本；源码搬动后重新安装宿主 skill 即可更新入口。

连接方式沿用用户已有选择；用户尚未说明时，先询问使用已有服务还是在本机启动，再按选择连接：

- 已有服务：使用选定的 URL 和凭据来源，在环境中成对提供 HAMLET_URL/HAMLET_TOKEN；随后用 `bash "$hamlet_cli" call whoami '{}'` 验证 Owner 身份。
- 本机启动：获得启动授权后，在选定的 HAMLET_HOME 执行 `bash "$hamlet_cli" start`，记录返回 URL，后续命令沿用该连接与数据目录。

未绑定连接的 call/ring/ui 会尝试发现或启动本机服务；`status` 只查询健康。连接失败时检查错误，不清除配置改连别处。凭据不放入 prompt、命令参数或共享记录。详细连接与错误处理见 [API](references/api.md#服务连接)。

## 创建、派发、查看结果

创建前，向用户展示方案：group、Agent 数量与任务分工、admin/worker 身份、模型、会话模式、目录、资源和触发方式。询问是否确认按此方案创建，以及是否要给某个 Agent 发送首条消息触发执行，并说明拟发送的目标和任务内容。已有明确确认直接沿用；按确认的方案创建和派发。配置及目录迁移语义见 [配置](references/configuration.md)。

在创建方案中同时写好各 Agent 的职责草稿和首条任务：system_prompt_tpl 描述长期目标、职责边界、交付标准与协作关系；消息 prompt 描述本次输入、行动和验收。可从 [角色与任务模板](references/role-prompts.md) 填写，删除不适用项，不将服务、凭据或工具用法复制到角色 prompt。prompt_tpl 只用于确有默认任务的场景，可留空。

以下命令展示创建与派发流程，实际参数以确认的方案为准。

1. 创建 group，或用 group_list 查询并复用已有组。

```bash
bash "$hamlet_cli" call group_create '{"name":"项目研究"}'
```

2. 将返回的 id 填入 GROUP_ID，并把 workdir 改为实际项目的完整路径，创建逻辑 Agent。

```bash
bash "$hamlet_cli" call agent_create --stdin <<'JSON'
{
  "group":"GROUP_ID", "role_name":"Researcher", "identity":"worker",
  "harness":"codex", "model":null, "harness_options":{},
  "run_mode":"fork", "workdir":"/path/to/project", "worktree":false,
  "system_prompt_tpl":"完成分配的研究任务，结论附文件路径和证据。",
  "prompt_tpl":"", "extra_env":{},
  "doorbell_script":"", "doorbell_enabled":false
}
JSON
```

Hamlet 会自动安装后台 Agent skill：Codex 使用工作目录中的 `.agents/skills/hamlet-agent`，Claude 使用 `.claude/skills/hamlet-agent`；创建实际 worktree 时也会安装。admin 每回合会收到按需阅读该 skill 的提示；CLI 环境由服务提供。无需给后台 Agent 复制这份 Owner 指南。

3. 若确认的方案包含首次派发，将目标的逻辑 id 填入 AGENT_ID，发送约定任务。

```bash
bash "$hamlet_cli" call message_to '{"target":"AGENT_ID","prompt":"阅读 README 并总结项目结构与测试命令，不修改文件。","dedup_key":"project-survey-v1","dedup_window":"7d"}'
```

4. 保存返回的消息 id，填入 MESSAGE_ID 查询；从 runs 获取运行 id 和实际 instance_id。

```bash
bash "$hamlet_cli" call message_get '{"id":"MESSAGE_ID"}'
bash "$hamlet_cli" call run_get '{"id":"RUN_ID"}'
bash "$hamlet_cli" call run_events '{"id":"RUN_ID","limit":100,"order":"desc"}'
```

入队回执不代表完成；成功结果在 run.final_output 及任务约定的文件中，失败原因在 error 中。查询不会触发执行。停止当前回合使用 `call run_stop '{"instance_id":"INSTANCE_ID"}'`；其余排队消息需单独取消。

## 消息与积压确认

message_to 没有 mode 参数。fork 模式的逻辑短 ID 创建新会话；真实长 ID resume 指定会话；resident 模式的逻辑 ID 首次创建、随后复用常驻会话。同一任务追问或修复时，使用查询得到的真实 instance_id。

**每条被接受的消息触发一轮 Agent 执行，给自己发消息也一样。** 消息排队，不会插入正在执行的回合。通知频率和协作方式按任务需要安排。

工具会检查目标会话的消息积压；拒绝时返回原因、排队数量和 details.message_id，并保存 rejected 记录。根据任务需要决定是否继续，确认推送时调用：

```bash
bash "$hamlet_cli" call message_force_push '{"id":"REJECTED_MESSAGE_ID"}'
```

它只把该条 rejected 消息加入队尾；未知或已推送 ID 会报错。Owner 可处理任何拒绝记录；后台实例只能强推自己发送的拒绝记录。是否继续由调用方根据任务决定，无需固定转交人类确认。

- 模板覆盖、配置快照、工作目录、资源：读 [配置](references/configuration.md)。
- 参数、权限、去重、错误与管理操作：读 [API](references/api.md)。
- 组织 Leader/Worker 与 doorbell：读 [协作示例](references/collaboration.md)。
