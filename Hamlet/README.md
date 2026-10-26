# Hamlet · 坊

Hamlet 管理独立的后台 Codex/Claude Agent：创建一组协作者，发送任务，通过消息继续会话，再查询执行记录与结果。daemon 持久化消息和运行，宿主会话结束后后台工作仍可继续。

- **group**：Agent 的通信与管理边界。
- **逻辑 Agent**：保存模型、角色、目录和会话模式，具有短 ID。
- **实例**：实际工作的模型会话，具有长 ID。
- **Owner**：代表用户管理服务的宿主助手，或使用 Owner 凭据的 CLI/Web UI 调用方。

## 安装与启动

需要 Node.js 20+、Git、Python 3.9+，以及已安装并登录的 Codex 或 Claude Code。

```bash
git clone https://github.com/yulangz/Hamlet.git
```

在需要使用 Hamlet 的项目根目录运行安装脚本。下面假设克隆的 Hamlet 目录位于该项目下：

```bash
HAMLET_PORT=34883 bash ./Hamlet/scripts/hamlet-install.sh
```

脚本使用现有源码安装依赖、构建和启动服务，并将 Owner skill 安装到当前项目的 `.agents/skills/hamlet`，最后输出 Web 登录入口。已有 skill 会保留，更新时检查本地修改后同步；脚本不会拉取源码。

| 环境变量 | 用途 |
| --- | --- |
| HAMLET_PORT | 首次安装指定端口；已有运行服务时可省略并沿用 |
| HAMLET_HOME | 数据库、凭据与日志目录，默认 ~/.hamlet |
| HAMLET_PUBLIC_URL | 可选的公开映射地址，用于生成登录入口 |
| HAMLET_RESTART=1 | 重启该数据目录的服务，保留数据 |

服务只监听本机地址。公开访问由外部端口映射或反向代理提供，例如：

```bash
HAMLET_PUBLIC_URL="https://hamlet.example.com" \
HAMLET_RESTART=1 bash ./Hamlet/scripts/hamlet-install.sh
```

## 使用入口

Owner 在宿主对话中调用 `$hamlet`，按 [Owner skill](skills/hamlet/SKILL.md) 完成连接、创建、派发和查询。安装后的 skill 自带 `scripts/hamlet`，其中已记录实际 CLI 路径。项目根目录可执行：

```bash
bash .agents/skills/hamlet/scripts/hamlet --help
bash .agents/skills/hamlet/scripts/hamlet status
```

也可直接在源码目录使用 `node bin/hamlet.js`。连接已有服务时成对设置 HAMLET_URL/HAMLET_TOKEN；未指定连接的 call/ring/ui 会自动发现或启动本机服务。凭据仅通过环境或认证头使用。

Owner skill 也可单独安装到其他宿主支持的目录：

```bash
# 在 Hamlet 源码根目录执行；路径可按宿主的 skill 目录调整
node bin/hamlet.js skill install --path "$HOME/.codex/skills/hamlet"
```

后台实例使用另一份 [Agent skill](skills/hamlet-agent/SKILL.md)。创建 Agent 时，Hamlet 自动将其放入工作目录：Codex 使用 `.agents/skills/hamlet-agent`，Claude 使用 `.claude/skills/hamlet-agent`；实际实例目录准备好后也会安装，并在运行时提供 skill 路径和 CLI 环境。已有非 Hamlet 管理的同名目录会报错，生成内容不会进入自动 Git 提交。

Hamlet 为每个子实例注册 `message_to` 和 `message_force_push` 模型工具，绑定当前实例身份；Codex 和 Claude 在 fork/resume 时都会加载。工具描述与参数定义见 [definitions.ts](packages/agent-tools/src/definitions.ts)。worker 的 skill 不介绍 CLI；admin 需要管理组员时按需阅读管理操作。

## 会话与消息

创建 Agent 只保存配置；发送消息才触发执行。`message_to` 没有 mode 参数：

| 目标 | 行为 |
| --- | --- |
| fork 模式逻辑短 ID | 新建实例和会话 |
| 真实实例长 ID | resume 指定会话 |
| resident 模式逻辑短 ID | 首次创建，之后复用常驻会话 |

每条被接受的消息触发一轮执行，包括自发消息；消息不会插入正在执行的回合。同一实例按 FIFO 运行，共用 direct 工作目录的实例全局串行，独立目录可并行。入队回执只表示提交成功，结果需查询运行记录。

提交前，目标真实实例已有 **超过 5 条 queued 消息**时（resident 逻辑 ID 共用常驻实例队列；fork 新会话不汇总其他实例），服务保存 rejected 消息并返回 409，包含拒绝 ID、原因和数量。已有 5 条时仍接受新消息。调用方确认继续时使用：

```json
{"id":"被拒绝的消息 ID"}
```

将该参数传给 `message_force_push` 即可把原消息加入队尾。后台实例只能强推自己发送的拒绝消息，Owner 可处理所有拒绝消息；未知或已推送 ID 会报错。Web UI 的消息队列也提供“确认推送”按钮。

## 工作目录、权限与配置

- `worktree:false`：直接使用指定目录，不移动文件、不自动提交。可以接入外部创建的 Git worktree。
- `worktree:true`：Hamlet 管理 main/shared/worktrees；Git 模式每实例分支、每回合自动提交，非 Git 模式使用文件副本。迁移非空目录需显式设置 migrate:true。
- Owner 管理所有组；admin 管理本组 worker；worker 在本组查询和通信。这些是 API 权限，工作目录隔离不构成文件系统沙箱。
- system_prompt_tpl 定义新实例职责；prompt_tpl 是默认任务，消息 prompt 可覆盖。已有实例保留配置快照，更新逻辑配置影响新实例。
- doorbell 是检测外部事件并发送消息的独立脚本。GPU 配额、作业调度和任务验收由协作方案或外部系统负责。

角色与任务草稿可从 [角色模板](skills/hamlet/references/role-prompts.md) 填写；消息状态与恢复规则见 [队列状态机](docs/message-lifecycle.md)，模型可见文案见 [prompt 索引](docs/agent-prompt-index.md)。

完整选项见 [配置](skills/hamlet/references/configuration.md)，工具参数、认证和错误见 [API](skills/hamlet/references/api.md)，Leader/Worker 交接见 [协作示例](skills/hamlet/references/collaboration.md)。

## 开发

在源码根目录执行：

```bash
corepack pnpm install --frozen-lockfile
corepack pnpm build
corepack pnpm typecheck
corepack pnpm test
corepack pnpm test:python
# UI 测试需先安装浏览器
corepack pnpm exec playwright install --with-deps chromium
corepack pnpm test:ui
```

测试范围及文件保留规则见 [开发与验证](docs/verification.md)。运行数据库、日志、凭据和生成的构建产物不提交到仓库。
