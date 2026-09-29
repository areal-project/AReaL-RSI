# AReaL-RSI

一个支持持续研究的多 Agent 系统：Interaction 与用户确定需求，RSI 负责调度，Research Agents 执行实验、汇报结果并在原会话中继续研究。新任务默认使用纯 pass@k，各研究员独立完成整题；只有显式配置协调模块时，才由 RSI 模型提供建议。工作台展示对话、研究进度、执行轨迹、作业和验证成绩。

核心实现统一位于 `src/areal_rsi/infra/`，公共契约位于 `api/`。
`ResearchSystem` 可由 Python、CLI 或工作台调用；Web 只是可选操作界面。

## 准备

需要 Python 3.11+ 和 uv。运行模式自动识别系统：macOS 默认使用 `sandbox-exec`；
Linux 默认使用 `linux-unrestricted`，Agent 以服务用户权限运行，不提供文件或进程隔离。
可在 `.env` 中设置 `RSI_RUNTIME_ISOLATION` 覆盖默认模式，详见[后端说明](docs/agent-backends.md)。
standard 后端还需要 Node.js 22.19+ 和 pnpm。standard/codex 的研究模型接口须支持
Responses API 和工具调用；其他后端使用对应的协议。

```bash
uv sync --locked
cp -n .env.example .env
```

在 `.env` 中配置模型连接，密钥仅保存在本机：

```dotenv
RSI_BASE_URL=https://4ipic.com
RSI_API_KEY=<你的密钥>
RSI_MODEL=gpt-6-astra
RSI_REASONING_EFFORT=medium
RSI_DATA_ROOT=.rsi/research-lab
```

## 启动 Web 工作台

在仓库根目录运行：

```bash
uv run --env-file .env --locked python run.py --type web
```

默认地址是 **http://127.0.0.1:8197**。指定其他端口，例如 33331：

```bash
uv run --env-file .env --locked python run.py --type web --port 33331
```

然后打开 **http://127.0.0.1:33331**。若服务在远程服务器上运行，先用开发环境的端口转发，
或在本机执行 `ssh -L 33331:127.0.0.1:33331 <服务器>`，再打开本机地址。
已有研究可直接访问 `http://127.0.0.1:33331/?run=<run_id>`。

首次使用可按下文[新建与查看研究](#新建与查看研究)操作；退出前参见[暂停、停止与删除研究](#暂停停止与删除研究)。

Agent 后端支持 `standard`（默认，DSH）、`codex` 和 `runtime`（受信 Runtime 配置，
可选择 Hermes、Claude 等适配器，也可按角色选择后端）。模型连接和后端配置在研究开始前
固定。所有后端通过 `PackageAgentRuntime` 执行；原生会话、工具和压缩由 Runtime 管理，
RSI 负责研究编排、权限、作业和验证。配置与隔离边界见[后端说明](docs/agent-backends.md)。

Codex 后端可选择“本地 Codex 登录”：无需填写 API key 或 API 地址，复用启动工作台
这台机器上 `$CODEX_HOME/auth.json`（默认 `~/.codex/auth.json`）的登录。
认证文件通过链接共享，由原生 Codex 更新；研究会话和配置仍使用独立目录。
配置标识为 `auth_mode: "codex_login"`；默认 `api_key` 保留原有 API 连接方式。
本地登录仅适用于 Codex（包括各研究角色均为 codex-cli 的 Runtime 配置），
不支持仅存储在系统钥匙串中的凭据；这种情况需先运行
`codex -c 'cli_auth_credentials_store="file"' login`。运行中的研究不会切换认证方式。

Web 新任务统一创建为 `"mode": "passk", "collaboration_modules": []`，默认 1 名研究员，
不再提供 v0.2.1 / v0.2.2 模式选择器。已有研究显示其原有协作配置；需要协调模块时，
通过完整研究配置显式指定，参见[协作配置](docs/collaboration.md)。
启动配置和模块版本会冻结；旧实验恢复须匹配其冻结版本。配置与比较方法见 [pass@k](docs/passk.md)。

研究图中每个 Agent 占一列，进展按实际发生时间向下展开，空白时段压缩；个人有效时间单独显示在研究员卡片中。默认展示成绩突破、汇报、建议和待完成作业，也可切换“只看协作”或展开全部作业。虚线根据建议附带的同一研究内的引用标明信息来源；点击事件可查看原文和送达状态。送达不代表已被采纳，没有明确同伴来源的消息显示为 RSI 的建议。此视图只读取历史记录，不改变 agent 协作或补写实验数据。

`rsi`、`rsi web` 和 `rsi workbench` 均以前台方式启动同一个新工作台；
`python run.py --type web` 负责后台启动或复用服务。新工作台、`rsi research` 和
`rsi preflight` 的数据目录统一按 `--data-root`、`RSI_DATA_ROOT`、`.rsi/research-lab`
的顺序选择。以前直接运行 CLI 保存在 `.rsi` 的研究，可用 `--data-root .rsi` 打开。
研究入口统一为 ResearchSystem；旧 `tui/run/resume/status/local-app`、TUI/MCP 控制器和
固定轮次调度器已移除。旧计划与检查点不迁移，请使用当前配置新建研究。

仓库入口自动在后台启动服务；已有兼容的 RSI 工作台则复用，其他进程占用端口则报错，不关闭或覆盖它。用 `--port 8200` 指定端口，用 `--data-root /path/to/data` 指定数据目录。旧版 RSI 服务需要先重启到当前版本。

使用 `run.py --type web` 后台启动时，服务日志和 PID 位于
`<数据目录>/runtime/workbench-<端口>.log` 和 `workbench-<端口>.pid`。
若希望前台运行、用 Ctrl+C 关闭服务：

```bash
uv run --env-file .env --locked rsi workbench --port 8197
```

## 新建与查看研究

1. 点击“新建研究”，填写目标、代码仓库或资料位置、允许使用的环境与资源，以及可检查的完成标准。
2. 按需选择 Agent 后端和模型连接；未指定研究员数量时默认 1 名，也可在对话中要求多人。
3. 与 Interaction 确认需求、验收方式和时间预算。普通研究可在对话中明确要求“最多探索一小时”；
   有“最长运行时间（小时；留空不限）”输入框的研发任务可直接填写。启动前在“研究约束”中核对实际保存的时限。
4. 条件准备好后，明确要求开始研究；如页面显示环境预检或启动按钮，按提示完成。开始后模型连接和协作配置锁定。
5. 在对话中查看正式回复和标记为“过程说明”的公开文字；可加载更早记录。
   “执行命令”“修改代码”等提示表示当前操作，不代表 Agent 的最终答复。
   “执行轨迹”可展开工具参数、输出和耗时；多研究员任务还可通过研究图查看各路进展。
6. 需要调整执行时，先选择消息接收者再发送指导；修改整组任务目标时使用“调整全局目标”，并核对验收覆盖。

## 暂停、停止与删除研究

| 操作 | 使用方式与效果 |
| --- | --- |
| 暂停后继续 | 点击“暂停”，需要恢复时点击“继续”。可定向暂停某个研究员；已提交的作业需在作业详情中单独取消。 |
| 停止研究 | 在研究页点击“停止”；未开始的对话可能显示“结束对话”或“放弃任务”。系统停止 Agent 并取消受管理的未完成作业，保留记录；用户主动停止后不会自动恢复。 |
| 停止所有研究 | 在研究列表中逐项停止仍在运行或暂停的研究，以及仍在收尾的任务，确认状态和作业已结束。尚在处理需求的对话也应结束。 |
| 删除研究 | 选中研究，点击“删除研究”，再点击“确认删除”。运行、暂停或收尾中的任务需先停止。永久删除该研究的本地对话、记录和工作目录，外部环境与共享数据不受影响。 |

关闭浏览器不会停止后台研究。希望稍后继续同一次研究时，优先使用“暂停”；停止后的恢复能力取决于
任务模式、冻结版本和收尾状态，不能假定停止后总能直接继续。

### 关闭或重启后台 Web 服务

关闭服务会中断它承载的 Agent 执行，但保留研究记录，且不等于将所有研究标记为“用户主动停止”。
若目的是结束任务，先按上面的步骤停止研究，再关闭服务。配置了自动恢复的任务可能在服务重启后继续。

以下以默认数据目录、33331 端口为例；使用了其他 `RSI_DATA_ROOT` 或 `--data-root` 时，先修改目录：

```bash
RSI_WEB_ROOT=.rsi/research-lab
RSI_WEB_PORT=33331
RSI_WEB_PID=$(cat "$RSI_WEB_ROOT/runtime/workbench-$RSI_WEB_PORT.pid")
ps -p "$RSI_WEB_PID" -o pid,args
```

核对 PID 对应本项目、指定端口的 `areal_rsi workbench` 进程，再发送正常退出信号：

```bash
kill -TERM "$RSI_WEB_PID"
```

等待该进程退出后，用原端口和数据目录重新启动，再刷新浏览器：

```bash
uv run --env-file .env --locked python run.py --type web \
  --port "$RSI_WEB_PORT" --data-root "$RSI_WEB_ROOT"
```

启动器会复用已有服务，不会自动重载新 Python 代码。升级代码后需完成上述重启；
同一数据目录建议只运行一个工作台。若另一端口或 CLI 仍持有研究执行权，应先在持有者处停止任务，
不能靠打开第二个工作台接管它。

## 统一的 pass@k 执行

单路与多路使用同一套 researcher 工具、目标上下文、人工指导、完成和阻塞逻辑。
前端统一使用 **pass@k**，不单独提供单路入口。Interaction 会先明确任务描述和验收标准，
必要时进行多轮沟通；研究员数量可选，未指定默认 1，可在对话中要求多人。
启动流程由内置 [rsi-intake skill](src/areal_rsi/resources/skills/rsi-intake/SKILL.md) 定义。
配置统一为 `"mode": "passk", "collaboration_modules": []`，单路将
`brief.limits.max_agents` 设为 `1`，多路设为需要的数量。

架构保留 `rsi-agent`。纯模式下它以代码执行启动、续跑、重试、接收报告和停止判断，
不创建 RSI 模型会话；只有显式启用协调分析模块时，才增加 RSI 的模型推理。
各路独立完成整题，不自动共享代码、经验或结果。

每路使用 `update_goal` 保存进度、下一步、验收条件和证据。blocked 等待补充，
completed 停止本路；所有研究员完成后结束整组。配置了可信目标时仍可按达标条件
提前停止整组，配置了最终确认时由系统执行。没有可信验证器的证据标注为 Agent 汇报。
不能完成时不要把一次模型回复当作完成，也不重复催促已阻塞或完成的研究员。

运行中可定向指导、暂停或继续某一路，也可修改全局目标。单路聊天框支持取消当前
执行后重新给指令；独立作业可单独取消。Web 展示连续进展，底层按研究员分别持久化
目标和每次执行记录，支持恢复与历史查看，不展示模型私有推理或认证数据。

旧 `mode=pass1` 记录仅供历史查看，不重写其数据、冻结配置或 source-lock；
新任务使用统一模式。旧版本冻结的实验应使用对应源码版本恢复。

### 研究何时结束

普通新研究默认不设置总时限或每名研究员的有效时限，`null` 表示不限；
没有设置时限时，运行很久不会仅因耗时而自动结束。当前 pass@k 要求
`max_total_tokens`、`max_verify_calls` 为 `null`，原生运行时的 `max_model_calls` 也只接受
`null`；不能通过这些字段限制次数或用量，应使用下面的时间预算。

| 条件 | 系统行为 |
| --- | --- |
| 正式验收达标 | 任一路通过正式验证且达到目标分数后，可停止整组探索；需先处理待送达的用户指令，配置了最终确认时还要完成独立复测。 |
| 所有研究员完成目标 | 各路通过 `update_goal` 标记完成，并提交验收条件和证据；配置了验证协议时还需已验证候选。全部完成后结束整组。 |
| 达到明确配置的时限 | `brief.limits.max_wall_seconds` 限制整组探索时长；`max_effective_seconds` 限制每名研究员的有效时间，全部耗尽后停止探索。有收尾配置时再进入验证和归档。 |
| 用户主动停止 | 终止研究并保留已有记录，不自动恢复或继续收尾。 |

通过完整配置设置一小时总探索时限时，在 `brief.limits` 中填写 `"max_wall_seconds": 3600`；
通过 Web 创建时，请在开始前明确预算并核对“研究约束”。`max_jobs` 是并发作业上限，不是累计实验次数。
有效时间如何扣除 API 故障与 GPU 排队，见[有效时间说明](docs/effective-time.md)。

一次模型回复结束、提交进度报告、作业显示 `succeeded`，都不等于整项研究已经完成。
作业成功只说明执行正常结束，验收还要看 `passed`、成绩和证据。
`blocked` 表示等待补充，不算成功，也不会自动结束其他研究员。
当前没有通用的“连续失败 N 次”或“长期无进展”自动停止保护；若反复失败且仍被标记为进行中，
应暂停检查验证日志、环境和参数契约，而不是等待系统必然自行结束。

## 一键运行 KernelBench pass@k demo

拥有 et15 SSH 和已部署 GPUQ 的访问权限后，无需先打开浏览器或重复填写需求：

```bash
uv run --env-file .env --locked python run.py --type demo
```

脚本自动启动或复用工作台，默认以纯 pass@k 启动 L3P10 的四名独立研究员，探索限时一小时、提交不限次数。结束后自动冻结最佳候选、独立复测并归档。关闭浏览器或启动终端不影响后台研究。

三题并行、每名研究员半小时有效时间的固定实验，见 [v0.2.1 示例](examples/kernelbench_artifacts/v0.2.1/README.md)。

题目、提交预算、环境检查、恢复命令和历史结果见 [KernelBench demo 快速指南](examples/kernelbench_artifacts/README.md)。这条入口优化具体 kernel，不需要额外 Task Agent；[通用 harness 优化](examples/harness/kernelbench/README.md)是另一种任务。

## CLI 与 API

已有完整研究配置时，可以不经过 Interaction，直接在终端运行：

```bash
uv run --env-file .env --locked rsi research --config /absolute/path/research.json
uv run --env-file .env --locked rsi research --resume <run_id>
```

CLI 持续输出进度；Ctrl+C 保存状态供恢复。用同一数据目录打开工作台即可查看。CLI 持有执行权期间，Web 只读；需要同时操作研究时，使用上面的 demo/API 入口，让工作台后台执行。

工作台提供本机 HTTP API。先 `GET /api/bootstrap` 获取 `csrf_token`，写请求使用 `X-RSI-Token` 请求头和 JSON body：

| 接口 | 用途 |
| --- | --- |
| `POST /api/runs`，body 为 `{"message":"研究需求"}` | 创建 Interaction 对话 |
| `POST /api/runs`，body 为 `{"run_id":"自定ID","config":{...}}` | 用完整 `ResearchConfig` 创建研究；同一 ID、同一请求可安全重试 |
| `POST /api/runs/<id>/start`，body 为 `{}` | 启动已准备的研究 |
| `GET /api/runs`、`GET /api/runs/<id>` | 查看列表、搜索图、报告、作业和成绩 |
| `POST /api/runs/<id>/chat`，body 为 `{"text":"补充要求"}` | 发送消息 |
| `POST /api/runs/<id>/pause`、`resume`、`stop` | 控制执行，body 为 `{}` |
| `POST /api/runs/<id>/delete`，body 为 `{}` | 删除已停止或其他可删除状态的研究记录 |
| `GET /api/runs/<id>/trace?agent_id=interaction&limit=50` | 查看指定 Agent 的公开执行轨迹；用返回的 `next_before` 作为 `before` 加载更早记录 |

## Harness 优化示例

[examples/harness](examples/harness/README.md) 收纳算术、LiveCodeBench 和 KernelBench
的通用 harness 优化。它们修改 prompt/skill，再由固定 Task 模型完成题目；
与主 demo 直接优化 kernel 源码是不同任务。

```bash
uv run --env-file .env --locked rsi prepare arithmetic --directory .rsi/math/setup
uv run --env-file .env --locked rsi research --config .rsi/math/setup/research.json
```

准备 LiveCodeBench 使用 `rsi prepare livecodebench --arena-config ... --directory ...`。
高级 KernelBench harness 接入见其独立说明；直接优化 kernel 继续使用上面的 demo。

## 检查研究运行时

```bash
uv run --env-file .env --locked rsi preflight --backend standard
uv run --env-file .env --locked rsi preflight --backend codex
# 也可传入实际研究配置：rsi preflight --config /absolute/path/research.json
```

预检使用生产工作台的 Runtime 工厂，实际调用配置的模型与任务工具，并检查进程重启后
的会话记忆、会话标识和重复消息回执。默认总超时 180 秒，可用 `--timeout` 调整。
记录写入数据目录下的 `preflight/`，不会创建正式研究或提交 GPU 作业。
旧 `scripts/preflight_responses.py` 命令转发到同一个预检。

## 记录与恢复

每次研究的数据集中在 `RSI_DATA_ROOT/runs/<run_id>/`，包含需求、会话、任务、报告、共享证据、冻结候选和验证记录。等待作业、报告或用户时不调用模型；临时 API 故障自动重试。

上下文窗口、原生重试和会话边界见[后端说明](docs/agent-backends.md)。
`brief.limits.max_model_calls` 保留可空字段，目前只接受 `null`；已移除的旧压缩/重试字段
会明确报错，不自动迁移旧配置或会话。

demo 在服务重启后自动恢复尚在执行的研究或收尾；手动暂停的研究保持暂停。完成最终复测后不能继续修改这次候选，需要新建 run。外部凭据失效或集群故障会明确报错或等待处理。

## 常见问题

| 现象 | 检查与处理 |
| --- | --- |
| 一直“正在读取研究记录”或请求超时 | 确认已重启到当前代码并刷新页面。查看浏览器 Network 中 `/api/runs/<id>` 的响应；记录多、共享存储慢时首次加载可能较慢，当前界面会在失败后提示并自动重试。 |
| 只看到“执行命令”，没有文字回复 | 对话里的“过程说明”来自 Agent 已产生的公开文字，不展示私有推理。查看执行轨迹；如果页面提示缺少轨迹接口，重启工作台后刷新。 |
| 作业成功，但研究一直不结束 | 检查验证记录的 `passed` 和目标成绩，再查看验证日志。核对验证包装器传入的参数是否被候选脚本接受；脚本退出码非零或报告缺失时不会通过验收。 |
| 提示由另一个进程控制 | 检查是否另有端口的工作台或 CLI 使用同一数据目录。在实际持有执行权的进程处停止任务，之后再使用当前工作台。 |
| 停止请求超时 | 刷新核对研究和作业状态，超时不代表停止操作完全没有执行。必要时按上面的步骤正常关闭持有者服务，检查残留 Agent 和作业，再重启查看。 |
| 不能删除研究 | 先停止运行、暂停或收尾中的任务，并等待作业资源释放；再点击“删除研究”。 |
| 旧研究无法恢复 | 检查冻结的协作版本、配置和源码版本；使用匹配版本恢复，或新建研究。不要修改旧记录或 source-lock 来绕过检查。 |

后台服务日志可这样查看（替换为实际数据目录与端口）：

```bash
tail -n 100 .rsi/research-lab/runtime/workbench-33331.log
```

服务日志没有异常堆栈不代表所有请求都成功；验证失败还需查看对应研究的作业输出和验证记录。

## 文档导航

启动方式与示例见本 README；按需阅读以下专题：

| 文档 | 内容 |
| --- | --- |
| [架构](docs/architecture.md) | 组件职责、消息、候选与数据流 |
| [接口](docs/interfaces/README.md) | ResearchConfig、领域 API、实验命令与验证契约 |
| [后端](docs/agent-backends.md) | Runtime 配置、工具桥、隔离、会话与测试 |
| [pass@k](docs/passk.md) | 独立尝试、预算、停止与实验比较 |
| [协作](docs/collaboration.md) | guidance 版本、权限、Prompt 来源与复现 |
| [有效时间](docs/effective-time.md) | API 故障和 GPU 排队的扣时规则 |
| [GPU 隔离](docs/gpu-queue-isolation.md) | KernelBench 队列部署及隔离边界 |

开发约束见 [AGENTS.md](AGENTS.md)。首次安装前端测试依赖：
`npm ci --prefix tests/frontend --ignore-scripts`。
`make check` 统一执行架构边界、Python 测试、Ruff、Mypy 与前端测试；
只检查模块依赖可运行 `make architecture`。
