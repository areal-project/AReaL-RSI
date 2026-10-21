---
name: hamlet
description: 使用 Hamlet 管理后台 Codex/Claude Agent、坊（group）、消息、工作目录和 doorbell，通过 CLI 或 HTTP 协作；用于需要独立后台会话及事件唤醒的任务。
---

# Hamlet · 坊

Hamlet daemon 持久化 group、逻辑 Agent、实例、消息和运行。宿主与子 Agent 都使用 CLI/HTTP，无需注册 MCP。先读 [配置与系统模型](references/configuration.md)；调用时按需读 [API](references/api.md)，并行协作读 [事件驱动示例](references/collaboration.md)。

## 先确定服务与启动授权

Skill 不保存固定服务地址、端口或凭据。本节的服务选择面向宿主 Owner；子 Agent 直接沿用 daemon 注入的受限连接，不重新选择或启动服务。Owner 使用 Hamlet 前，从当前请求或已有明确授权中确定一种连接方式：

- **连接现有服务**：由用户提供或明确选定服务 URL，以及凭据来源（已配置的环境或已授权的本机数据目录）。只有 URL 不代表已有访问权限；缺少连接信息时先询问，不自行选择本机服务。
- **Owner 现场启动**：用户明确授权 Owner 启动本机服务后，按已授权的数据目录或用户允许的默认目录执行 `hamlet start`，读取返回的实际 `url` 并告知用户。端口动态分配，不猜测或沿用旧端口。`start` 可能复用同一数据目录下的现有 daemon；用户要求全新服务时，使用获授权的独立数据目录。

记录服务选择、地址来源、凭据来源和启动授权，不记录凭据值。缺项与其他必要配置合并询问，已有明确授权不重复确认。仅连接或启动 daemon 时，无需填写 Agent 的模型、目录和运行模式。

连接现有服务时，把用户选定的 URL 与相应凭据成对绑定到 `HAMLET_URL` / `HAMLET_TOKEN`，或用认证 HTTP 请求；这样连接失败会报错，不会自动另起本机服务。不得用未绑定连接的 `call`、`ring` 或 `ui` 探测服务，因为它们可能自动启动 daemon。`daemon.json` 仅用于解析或核对已获授权的本机连接，不是选择服务或启动服务的授权。连接步骤见 [API：服务连接](references/api.md#安装发现启动)。

## 创建前：先明确配置

创建、初始化、迁移或启动 group/Agent 前，整理配置表：**参数、明确值或不适用原因、来源（本次请求/此前明确授权/用户明确授权的默认值）**。不得把用户未提及当作授权，也不得静默借用服务端默认值。该表应包含以下全部项目；条件参数也必须明确适用性：

- group（新建时名称、复用时 ID）、role_name、identity（admin/worker）、职责及权限。
- harness、model、harness_options（包括思考强度或明确空对象）、run_mode（fork/resident）。
- workdir 绝对路径、worktree、git_init、适用的 base_ref、非空目录处理及 migrate。
- prompt_tpl、system_prompt_tpl、extra_env（无则明确 `{}`）、GPU/其他资源分配及共享约束。
- doorbell 是否启用、脚本绝对路径或不适用、触发条件、计时起点；仅创建还是创建后启动，以及首次任务和去重策略。

提取用户已有选择，直接沿用，不重复询问。对缺项先解释实际行为和影响，再合并询问；可以建议选项，必须由用户选择。**未回答前，不执行相关创建、初始化、迁移或启动，包括为了“先准备好”而新建 group。** 可读取现有配置和目录，检查 help/Skill，列出拟议请求。用户只给人数、模型、任务时，仍需问 run_mode/worktree/目录/身份等缺项。完整配置已授权后直接执行，无需再确认一次。

配置表中明确不适用的条件参数应从请求省略；明确空值使用真实支持的值（如 model:null、模板空串、extra_env:{}、doorbell_script:""）。资源/GPU、角色职责、触发条件和启动策略是协作约定，不是新增 daemon 字段。把资源放入 extra_env 和职责模板，脚本实现触发；不能编造 API 参数。

## 连接与身份

- 宿主先定位安装目录，用 `node /absolute/Hamlet/bin/hamlet.js`；可用 `hamlet skill path` 查询 Skill 路径。安装位置不决定服务 URL，所有操作沿用上面已选定的连接。
- 子 Agent 先读取 `$HAMLET_SKILL_DIR/SKILL.md`，使用 `"$HAMLET_NODE" "$HAMLET_CLI" call whoami '{}'` 确认身份。daemon 通过环境提供 HAMLET_URL/HAMLET_TOKEN、实例 ID；不要读取/替换本机 Owner 凭据、清除身份环境或给子 Agent 传 Owner token。不得将 token 写进 prompt、命令参数、共享记录或日志。HTTP 从环境读取 Authorization，勿用会打印请求头的诊断。
- Owner 管理所有坊；admin 管理本坊 worker；worker 在本坊查询与通信。权限由 daemon 每次判断，CLI 不提升身份。权限边界不是文件系统沙箱；子进程仍可访问宿主用户可访问的文件。
- 任何命令失败先看退出码和 `{error,details}`，不要换 Owner 身份重试。凭据不完整时 CLI 拒绝回退 Owner。

## 执行与收尾

创建返回逻辑 ID，不代表运行；message_to/ring 返回消息入队记录，不代表调度或完成。每条消息触发一个 fork/resume 回合，运行中的 Agent 在后续回合才读到新消息。**“90 秒后检查”这样的 prompt 不产生延迟；不得给自己反复发消息轮询。** 查询不会唤醒 Agent。

采用“提交一次任务 → 结束当前回合等待约定完成事件 → 事件唤醒一次 → 读取共享结果”。没有内置终态回调；必须在任务中约定 worker 写结果后发送一次带版本的通知，或预先配置 doorbell 检查外部事件。进度、RUNNING、ACK、重复确认写状态，不发执行消息。仅新的终态结果、真正阻塞或需要决策才通知。Leader 集中读取当前版本结果，按需分派下一轮，忽略已处理/过期任务，不回复无意义确认。

在共享状态记录 task_id、version、assignee、状态、结果路径、已处理通知和结束条件；每个新事件使用稳定 dedup_key。它是窗口内的消息去重，不是 exactly-once 执行保证。结束时写终态，停止 doorbell，按已授权的保留策略停止运行、归档/删除 Agent 或 group；停止 daemon 会影响所有坊，不能当作单任务清理。

不要承诺 daemon 尚未实现的 pending 合并、每 Agent 一个待唤醒、批量消费、原生延迟或终态回调；Skill 只指导协作协议，队列仍逐条 FIFO。提醒到点仅表示脚本尝试入队，实际读取还受目录锁、FIFO、暂停和运行时间影响。
