# 开发与验证

## 从源码运行检查

在仓库根目录执行：

```bash
corepack pnpm install --frozen-lockfile
corepack pnpm exec playwright install --with-deps chromium
corepack pnpm check
```

`check` 顺序执行 TypeScript 类型检查、生产构建、Node.js 核心与集成测试、Python 测试、Chromium 页面测试。浏览器与系统依赖仅需在首次使用或版本更新时安装。

日常修改可单独运行 `corepack pnpm typecheck`、`corepack pnpm test`、`corepack pnpm test:python`、`corepack pnpm test:ui`。涉及 CLI 或浏览器测试时，先运行 `corepack pnpm build`，以验证生成的入口。

## 测试覆盖

| 测试文件                    | 覆盖内容                                                                                         |
| --------------------------- | ------------------------------------------------------------------------------------------------ |
| `tests/core.test.ts`        | 身份与权限、模板和快照、fork/resident、FIFO 与 direct 目录串行、去重、Git/non-Git 工作目录及合并 |
| `tests/integration.test.ts` | HTTP/SSE、认证、真实 CLI 子进程、Codex/Claude harness 契约、CLI/HTTP 调用与权限、doorbell 脚本  |
| `tests/lifecycle.test.ts`   | 并发启动、宿主退出后 daemon 存活、硬崩溃恢复、遗留子进程和脚本恢复                               |
| `tests/observation.test.ts` | 排队原因、消息来源、Owner 查询及分页、历史快照、旧库迁移和损坏日志尾行                           |
| `tests/branding.test.ts`    | Hamlet 命名、新旧 CLI/环境变量/数据目录/工作目录标记及认证兼容                                   |
| `tests/codex-shell.test.ts` | 真实 Codex 命令环境的 fork/resume：身份保留、过滤/替换时启动前拒绝、安全 allowlist 不放宽、CLI 无 Owner 提权 |
| `tests/skill-cli.test.ts` | Skill 安装/拒绝覆盖、缺失凭据拒绝 Owner 回退、无 MCP 的 Codex/Claude 子进程 CLI 身份与权限、通信及 fork/resume |
| `tests/doorbell_test.py`    | Python ring 请求、持久状态与定时入口                                                             |
| `tests/ui*.e2e.ts`          | Chromium 页面中的配置、脚本、实例历史、diff/merge、消息重试、执行语义与阻塞原因                  |

常规测试使用受控 harness 或本地 CLI fixture，不调用付费模型。测试使用临时状态目录，不应指向自己的运行数据库。

## 可选的真实 Codex 验证

验证真实 Codex 命令环境（无需登录，不消耗模型额度）：

```bash
HAMLET_CODEX_SHELL_TEST_BIN=codex corepack pnpm exec tsx --test tests/codex-shell.test.ts
```

该回归通过真实 `codex app-server` 的 `command/exec` 路径验证命令环境，而不是直接由 fixture 继承环境：fork/resume × 用户/受信任项目/两种 harness_options × inherit all/core/none、exclude、include_only、自动 secret 过滤、set 替换和 keyed filters。安全配置的实际 CLI 返回 worker，Owner 操作被拒绝；过滤或替换身份的配置在 Harness.start/resume 内直接失败，模型不启动；安全的窄 allowlist 仍过滤无关 secret。测试用临时 HOME/.hamlet 放置真实 Owner daemon，避免“找不到 Owner”掩盖身份丢失。未设置变量时，常规测试明确跳过该真实 Codex 套件。

已删除旧 MCP server 包、SDK、配置列表探测和占位修复；对应的传输权限与消息/运行查询通过 CLI/HTTP 验证。生产启动直接使用 Codex CLI 参数，并执行本地 app-server 环境探针：仅对 runtime 身份和路径计算摘要，在私有管道比较，外层只接收成功标记；不回显 app-server 诊断，不把 token 值传入 argv、prompt 或 JSON-RPC。保留原环境策略，不能验证时拒绝启动。检查增加一次短生命周期 app-server，探针内部 7 秒、外层 9 秒超时；需要支持 command/exec 的 Codex（本机验证为 0.163.0）。不写用户配置、不向 daemon 发业务请求。

再按需进行会调用模型的完整 smoke 验证：

```bash
node scripts/smoke-codex.mjs
```

需要本机已安装并登录 Codex，会消耗模型额度。此脚本在临时目录验证新会话、通过 CLI 执行的受限 `whoami` 身份、上下文 resume，并在退出时关闭测试 daemon。它不属于常规 `check`。

Claude 的常规测试验证 CLI 参数、事件与调度契约；真实模型调用需另行在已登录的目标环境验证。Linux 是当前自动验证平台；macOS 的 APFS clone 与 launchd 分支需在目标机器验证。

## 仓库文件保留规则

- 提交 `packages/` 源码与包配置、`bin/` 可执行入口、`scripts/`、`tests/` 及 fixture、README 和开发文档、工作区配置与 `pnpm-lock.yaml`。
- 保留可执行文件的 Git 权限；保留 `bin/detached.js` 等兼容入口，以支持已有接入。
- `node_modules/`、各包 `dist/`、测试报告、截图、Python 缓存等由工具生成，留在本地并由 `.gitignore` 排除。
- 数据目录、SQLite 数据库、运行历史、日志、令牌及项目本地模型配置不入库。更换代码路径时，保持 `HAMLET_HOME` 一致，运行数据与源码目录独立。
- 设计文档及本地部署复盘不随源码发布；需要保留时放在仓库外。不要使用 `git add -f` 绕过这些规则。

发布前用 `git status --short`、`git diff --cached --stat` 和 `git ls-files --stage` 核对提交内容与入口文件权限。克隆后安装依赖并重新构建即可生成运行入口。

## Skill 验证

`skills/hamlet/SKILL.md` 是入口，references 分别维护配置、API、协作流程。人工核对两种情景：只给人数/模型/任务时停在配置缺项询问；已有完整授权时直接生成真实请求、不重复确认。不能用只匹配文案的测试声称模型一定服从 Skill；自动测试覆盖真实接口和身份路径，行为约束仍由加载 Skill 的调用 Agent 遵循。

服务连接另核对三种情景：未提供 URL 且未授权启动时先询问、不发自动启动的调用；用户提供 URL 时使用对应凭据，失败不改连其他服务；用户已授权 Owner 本机启动时读取 `start` 返回的动态 URL，并明确复用已有 daemon 与全新数据目录的区别。Skill 和安装副本均不得固化当前部署端口或凭据。

构建后可在无 MCP 配置的宿主用 `hamlet skill install --path /absolute/skills/hamlet` 安装，再执行明确授权的创建/触发/查询/消息/收尾。常规测试用 fixture 子进程实际调用 CLI，不消耗模型额度；不代表已执行真实 Codex/Claude 的模型任务。
