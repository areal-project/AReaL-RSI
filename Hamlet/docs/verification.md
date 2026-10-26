# 开发与验证

## 从源码运行检查

在仓库根目录执行：

```bash
corepack pnpm install --frozen-lockfile
corepack pnpm exec playwright install --with-deps chromium
corepack pnpm check
```

`check` 顺序执行 TypeScript 类型检查、生产构建、Node.js 核心与集成测试、Python 测试、Chromium 页面测试。浏览器与系统依赖仅需在首次使用或版本更新时安装。

共享存储上 CLI fixture 冷启动较慢时，可设置 `HAMLET_TEST_WAIT_MS=120000` 扩大测试的异步等待上限；不改变生产服务超时。

日常修改可单独运行 `corepack pnpm typecheck`、`corepack pnpm test`、`corepack pnpm test:python`、`corepack pnpm test:ui`。涉及 CLI 或浏览器测试时，先运行 `corepack pnpm build`，以验证生成的入口。

## 测试覆盖

| 测试文件                    | 覆盖内容                                                                                         |
| --------------------------- | ------------------------------------------------------------------------------------------------ |
| `tests/core.test.ts`        | 身份与权限、模板和快照、fork/resident、FIFO 与 direct 目录串行、去重、Git/non-Git 工作目录及合并 |
| `tests/integration.test.ts` | HTTP/SSE、认证、真实 CLI 子进程、Codex/Claude harness 契约、CLI/HTTP 调用与权限、doorbell 脚本  |
| `tests/public-url.test.ts` | 公开 HTTP/HTTPS 地址、平台 Host 改写、Cookie 登录与 API、Host/Origin 白名单、无效配置和后台 CLI 的公开入口 |
| `tests/lifecycle.test.ts`   | 并发启动、宿主退出后 daemon 存活、硬崩溃恢复、遗留子进程和脚本恢复                               |
| `tests/observation.test.ts` | 排队原因、消息来源、Owner 查询及分页、历史快照、旧库迁移和损坏日志尾行                           |
| `tests/branding.test.ts`    | Hamlet 命名、新旧 CLI/环境变量/数据目录/工作目录标记及认证兼容                                   |
| `tests/codex-shell.test.ts` | 真实 Codex 命令环境的 fork/resume：身份保留、过滤/替换时启动前拒绝、安全 allowlist 不放宽、CLI 无 Owner 提权 |
| `tests/message-pressure.test.ts` | 积压阈值、拒绝记录、强推权限与 FIFO、重复调用、重启持久化、CLI/HTTP 错误详情 |
| `tests/agent-skill.test.ts` | direct/Git/非 Git 自动安装、实例 skill 路径、Codex/Claude 目录、自定义文件与符号链接保护 |
| `tests/skill-cli.test.ts` | Skill 安装/拒绝覆盖、缺失凭据拒绝 Owner 回退、Codex/Claude 子进程 CLI 身份与权限、通信及 fork/resume |
| `tests/doorbell_test.py`    | Python ring 请求、持久状态与定时入口                                                             |
| `tests/ui*.e2e.ts`          | Chromium 页面中的配置、脚本、实例历史、diff/merge、消息重试、执行语义与阻塞原因                  |

常规测试使用受控 harness 或本地 CLI fixture，不调用付费模型。测试使用临时状态目录，不应指向自己的运行数据库。

## 可选的真实 Codex 验证

验证真实 Codex 命令环境（无需登录，不消耗模型额度）：

```bash
HAMLET_CODEX_SHELL_TEST_BIN=codex corepack pnpm exec tsx --test tests/codex-shell.test.ts
```

该回归通过真实 `codex app-server` 的 `command/exec` 路径验证命令环境，而不是直接由 fixture 继承环境：fork/resume × 用户/受信任项目/两种 harness_options × inherit all/core/none、exclude、include_only、自动 secret 过滤、set 替换和 keyed filters。安全配置的实际 CLI 返回 worker，Owner 操作被拒绝；过滤或替换身份的配置在 Harness.start/resume 内直接失败，模型不启动；安全的窄 allowlist 仍过滤无关 secret。测试用临时 HOME/.hamlet 放置真实 Owner daemon，避免“找不到 Owner”掩盖身份丢失。未设置变量时，常规测试明确跳过该真实 Codex 套件。

身份权限与消息/运行查询通过 CLI/HTTP 验证。生产启动直接使用 Codex CLI 参数，并执行本地 app-server 环境探针：仅对 runtime 身份和路径计算摘要，在私有管道比较，外层只接收成功标记；不回显 app-server 诊断，不把 token 值传入 argv、prompt 或 JSON-RPC。保留原环境策略，不能验证时拒绝启动。检查增加一次短生命周期 app-server，探针内部 30 秒、外层 35 秒超时；需要支持 command/exec 的 Codex（本机验证为 0.163.0）。不写用户配置、不向 daemon 发业务请求。

再按需进行会调用模型的完整 smoke 验证：

```bash
node scripts/smoke-codex.mjs
```

需要本机已安装并登录 Codex，会消耗模型额度。此脚本在临时目录验证新会话、通过 CLI 执行的受限 `whoami` 身份、上下文 resume，并在退出时关闭测试 daemon。它不属于常规 `check`。

Claude 的常规测试验证 CLI 参数、事件与调度契约；真实模型调用需另行在已登录的目标环境验证。Linux 是当前自动验证平台；macOS 的 APFS clone 与 launchd 分支需在目标机器验证。

## 仓库文件保留规则

- 提交 `packages/` 源码与包配置、`bin/` 可执行入口、`scripts/`、`tests/` 及 fixture、README 和开发文档、工作区配置与 `pnpm-lock.yaml`。
- 保留 CLI 和 skill 脚本的 Git 可执行权限。
- `node_modules/`、各包 `dist/`、测试报告、截图、Python 缓存等由工具生成，留在本地并由 `.gitignore` 排除。
- 数据目录、SQLite 数据库、运行历史、日志、令牌及项目本地模型配置不入库。更换代码路径时，保持 `HAMLET_HOME` 一致，运行数据与源码目录独立。
- 设计文档及本地部署复盘不随源码发布；需要保留时放在仓库外。不要使用 `git add -f` 绕过这些规则。

发布前用 `git status --short`、`git diff --cached --stat` 和 `git ls-files --stage` 核对提交内容与入口文件权限。克隆后安装依赖并重新构建即可生成运行入口。

## Skill 验证

`skills/hamlet/SKILL.md` 是 Owner 入口，`skills/hamlet-agent/SKILL.md` 是后台实例入口；描述需包含触发场景。Owner 安装脚本提供真实 CLI 路径，后台 skill 在创建和运行时自动安装到工作目录。references 提供按需阅读的配置与 API。人工核对两种情景：配置不完整时沿用上下文、说明常规实现选择，仅询问实质影响任务的缺项；已有完整授权时直接生成真实请求、不重复确认。不能用只匹配文案的测试声称模型一定服从 Skill；自动测试覆盖真实接口和身份路径，行为约束仍由加载 Skill 的调用 Agent 遵循。

服务连接另核对三种情景：未提供 URL 且未授权启动时先询问、不发自动启动的调用；用户提供 URL 时使用对应凭据，失败不改连其他服务；用户已授权 Owner 本机启动时读取 `start` 返回的动态 URL，并明确复用已有 daemon 与全新数据目录的区别。Skill 和安装副本均不得固化当前部署端口或凭据。

构建后可在宿主用 `hamlet skill install --path /absolute/skills/hamlet` 安装，再执行明确授权的创建/触发/查询/消息/收尾。常规测试用 fixture 子进程实际调用 CLI，不消耗模型额度；不代表已执行真实 Codex/Claude 的模型任务。

Agent 消息工具的描述与 schema 位于 `packages/agent-tools/src/definitions.ts`；`tests/agent-tools.test.ts` 验证实际 stdio/HTTP 转发、实例身份、消息拒绝与强推。用本机 Codex 验证工具发现和调用（不启动模型回合）：

```bash
HAMLET_CODEX_TOOLS_TEST_BIN="$(command -v codex)" corepack pnpm exec tsx --test tests/agent-tools.test.ts
```

`docs/agent-prompt-index.md` 是本次审阅的完整文案快照，包含源码位置和生效时机；它不会加载到 Agent。修改 runtime、skill 或工具描述后，审阅时应以相应源文件为准。

消息状态机审查与边界说明见 [message-lifecycle.md](message-lifecycle.md)。`tests/message-lifecycle.test.ts` 覆盖发送者去重隔离、重试积压检查、拒绝取消、实际路由、派发快照、归档/删除竞态、常驻启动失败、重启恢复、group 删除期间的并发请求与停止终态。
