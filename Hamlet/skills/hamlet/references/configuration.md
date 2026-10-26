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

Owner 使用 hamlet skill，后台实例使用自动安装的 hamlet-agent skill；daemon 不给 Owner 注入运行时 prompt。group 没有独立 prompt 字段。admin/worker 定义系统权限，业务职责由用户配置。建议在 system_prompt_tpl 写目标、职责边界、交付标准和交接方式，在每次消息中写任务版本、输入与输出路径；不必复制 runtime 和本 skill。

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

脚本崩溃按 1–60 秒退避，10 分钟内第六次崩溃进入 failed，start/restart 可复位。daemon 重启保留 enabled 状态并恢复脚本。脚本示例与收尾步骤见 [协作](collaboration.md)。
