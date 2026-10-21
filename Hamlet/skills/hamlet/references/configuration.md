# 配置与系统模型

## 服务连接先于 Agent 配置

先明确连接选择：用户提供或明确选定的服务 URL、凭据来源，以及是否授权 Owner 现场启动。本机启动还需明确使用的数据目录或用户允许的默认目录；实际 URL 在启动返回后记录，不要求用户预知动态端口。连接现有服务不包含启动许可，CLI 的自动发现/启动能力不代替用户授权。

只记录 URL、地址来源和凭据来源，不记录令牌。服务地址由每次连接/启动的实际结果决定，不写入 Skill。详细连接步骤见 [API](api.md#安装发现启动)。只连接或启动服务时，下面的 Agent 配置不适用；需要创建 Agent 时再提取完整配置，缺项可与连接信息合并询问。

## 会话、队列和目录

group 是通信/管理边界。逻辑 Agent 保存配置；实例保存会话和创建时的配置快照；run 是一次模型执行，message 是一次持久触发及其重试记录。

- `run_mode:"fork"`：每次以逻辑 ID/角色名触发都会创建新实例，会话不继承旧实例；共享文件是跨回合交接依据。
- `run_mode:"resident"`：首次触发才创建实例，后续触发 resume 已有会话。创建本身不运行。长 ID（如 abcdef-1）无论模式都指定 resume 那个实例。归档后不能继续触发。`instance_renew_resident` 归档旧常驻实例，下次逻辑触发才创建新实例。
- 同一实例消息按 FIFO 串行。不同实例可以并发，但 `worktree:false` 的相同 canonical/真实目录全局串行（跨 group 也如此）。不要在持有同一 direct 目录的回合内等待另一个 Agent 的 ACK。
- `worktree:false` 为 direct，直接使用指定目录，不移动已有内容；不创建 main/shared/worktrees 布局，不自动提交。git_init、base_ref、migrate 对 direct 不适用。不同 direct 目录各自并发，仍需约定对外部共享文件的写入责任。
- `worktree:true` 使用 root 下 `main/`、`shared/`、`worktrees/`、`.hamlet/workdir.json`。新空目录 `git_init:true` 初始化 Git；false 使用非 Git 快照。首次遇非空目录，`migrate:false` 返回 409 needs_migration 和摘要，不移动文件；只有用户明确允许迁移才传 true，将所有原内容移到 main/。用户未授权迁移就停止相关操作并询问。
- Git 实例从 main 的 HEAD 或明确的 `base_ref`（分支/提交）创建 `agent/<实例ID>`；未提交改动不带入，会记录警告。每次回合结束自动提交实例改动。已有 Git 的非空迁移沿用 Git；git_init 只控制空目录初始化。non-Git 从 base/ 复制，并保存每实例基准；workdir_refresh 只改变之后的 fork。base_ref 仅 Git 适用。
- worktree 元数据和 base_ref 属于工作目录配置，复用目录时读取已有值，不假定传新参数能重新初始化。模式不能混用。归档 Git 实例会删 worktree、保留分支；non-Git 删实例副本和基准；direct 原目录保留。harness 会话文件不清理。删除逻辑 Agent 会停止运行、归档并取消 queued 消息；group_delete 对组内全部 Agent 执行删除。
- 更新逻辑 Agent 的模型、参数、模板、环境仅作用于之后的 fork；已有实例继续使用快照。换新常驻实例需明确授权，避免丢失会话上下文。传给 resume 的 system_prompt 被忽略并记录警告。

## 配置表的填法

每项用“值｜来源”，条件项用“不适用及原因｜来源”。例如完整授权的 direct 研究任务：

| 参数 | 明确值 | 来源示例 |
| --- | --- | --- |
| group、role_name、职责 | 现有 g-xxxx，Research1，研究主题 A | 用户指定的坊和分工 |
| identity | worker | 用户指定只需本组通信 |
| harness、model | codex、用户给定模型 | 当前请求 |
| harness_options | {"model_reasoning_effort":"high"} | 已授权思考强度 |
| run_mode | fork | 用户选择每次全新会话 |
| workdir、worktree | /absolute/research/r1、false | 用户指定各自目录 |
| git_init、base_ref、migrate | 不适用：direct，不做初始化/迁移 | worktree 选择及目录现状 |
| prompt_tpl、system_prompt_tpl | 任务模板及职责全文 | 当前任务和已授权协作方案 |
| extra_env、资源 | {}，CPU，无 GPU，shared 目录仅各写自己的结果 | 用户授权资源分工 |
| doorbell_enabled、doorbell_script | false、无脚本 | 用户选择结果通知 |
| 触发/计时起点 | 不适用：不用定时器，完成后通知 Leader | 用户批准的协议 |
| 启动 | 创建后发一次 task-v1，dedup_window=7d | 用户指定开始任务 |

如果用户只说“4 个研究员加 Leader，用模型 M 研究 X”，上表许多项未知。先合并询问：身份/职责、fork 还是 resident、共享 direct 还是独立目录/worktree、绝对路径及迁移许可、思考强度、模板/环境/资源、doorbell/完成通知和启动时机。不能先替用户选择再创建。

只有模型具体 ID、版本和可用性取决于已安装 CLI/账号；不要替换用户模型。codex 的 `harness_options` 作为 `-c key=TOML` 传递（如 model_reasoning_effort）；Claude 当前只读取 `effort`，不能把 Codex 参数原样用于 Claude。Codex 启动前会通过真实命令环境探针核对受限身份和 CLI 路径；shell_environment_policy 若移除或替换 HAMLET/DETACHED 身份、路径变量，或无法完成检查，就拒绝启动。不会擅自改为 inherit=all、扩大 allowlist 或把 token 写入配置；读取错误后由用户决定如何调整过滤策略。`model:null` 表示用户明确授权使用 CLI 配置中的模型，不是“没有问”。

内置模板变量：`agent_id`、`role`、`group`、`from`、`from_role`、`worktree`、`shared_dir`、`now`，以及 ring/message_to 的 vars。未知变量会在分配实例前报错。直接传 prompt 会替代 prompt_tpl；system_prompt 只在新会话生效。不能将 token 放入模板或 vars。

## doorbell 与资源

Doorbell 是独立可执行脚本，必须明确脚本绝对路径、是否 enabled、事件条件、去重键和计时起点。传脚本且 enabled=true 时创建 Agent 就可能启动脚本，不代表“仅创建无副作用”。可先以 enabled=false 创建，再在用户指定时机 doorbell_control start。无脚本时 `doorbell_script:""`、`doorbell_enabled:false`。

GPU/其他资源没有 daemon 调度或配额字段。用户选择后可用 extra_env 的 CUDA_VISIBLE_DEVICES 和任务约定，明确同卡共享/独占与释放条件；这不是 daemon 强制隔离。脚本收到独立的 DOORBELL_URL/TOKEN，只能 ring 自己的逻辑 Agent/实例，不能查询工具或获得 Owner。

Python `every` 的计时从脚本顶层完成后开始；重启会重新计时。如果用户要求从提交时间/外部截止时间计时，必须持久化该时间并由脚本计算剩余时间。脚本到点 ring 不保证 Agent 在该时刻实际执行。崩溃退避 1–60 秒，10 分钟内第六次崩溃进入 failed；人工 start/restart 可复位。停止 daemon 保留 enabled 状态，下次启动恢复。
