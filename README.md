# AReaL-RSI

一个支持持续研究的多 Agent 系统：Interaction 与用户确定需求，RSI 制定计划并协调 Research Agents，Research Agents 执行实验、汇报结果并在原会话中继续研究。工作台按 Agent 展示纵向研究时间线、信息共享、作业进度、耗时与验证成绩。

## 准备

需要 Python 3.11+ 和 uv。默认隔离模式需要 macOS `sandbox-exec`；可信 Linux
服务器可在 `.env` 中显式设置 `RSI_RUNTIME_ISOLATION=linux-unrestricted`，
Agent 将以服务用户权限运行，不提供文件或进程隔离，详见[后端说明](docs/agent-backends.md)。
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
RSI_MODEL=gpt-5.6-luna
RSI_REASONING_EFFORT=xhigh
RSI_DATA_ROOT=.rsi/research-lab
```

## 第一次完整研究

建议先运行内置 Arithmetic 示例。它只需要研究模型 `RSI_*` 和被测模型
`RSI_TARGET_*`，不依赖 GPU、SSH 或 EnvArena，并覆盖候选保存、TRAIN 验证和独立
VALIDATE 最终复测：

```bash
uv run --env-file .env --locked python -m areal_rsi.adapters.benchmark_research prepare \
  --benchmark math --directory .rsi/arithmetic/setup
uv run --env-file .env --locked rsi research \
  --config .rsi/arithmetic/setup/research.json --data-root .rsi/arithmetic
```

命令会持续输出 run ID、状态和成绩；中断后使用输出中的恢复命令继续。完整记录位于
`.rsi/arithmetic/runs/<run_id>/`，也可以用相同 `--data-root` 打开工作台查看。

其他任务按依赖和优化对象列在[示例目录](examples/README.md)中。Runtime profile、数据组件
和集群部署资产与完整 ResearchApp 示例分开标注，避免把配置片段误认为可直接运行的研究。

## 启动 Web 工作台

在仓库根目录运行：

```bash
uv run --env-file .env --locked python run.py --type web
```

打开 **http://127.0.0.1:8197**，新建研究，与 Interaction 讨论任务、环境、资源限制和目标，要求开始后交给 RSI。开始前可选择 Agent 后端，并修改模型地址、密钥、名称和推理强度；开始后任务和协作配置锁定，可查看进展或明确停止。

Agent 后端支持 `standard`（默认，DSH）、`codex` 和 `runtime`（受信 Runtime 配置，
可选择 Hermes、Claude 等适配器，也可按角色选择后端）。模型连接和后端配置在研究开始前
固定。所有后端通过 `PackageAgentRuntime` 执行；原生会话、工具和压缩由 Runtime 管理，
RSI 负责研究编排、权限、作业和验证。配置与隔离边界见[后端说明](docs/agent-backends.md)。

新实验可设置 `"mode": "passk"`：RSI 启动 k 个独立完成整题的 researcher，接收进展并督促持续探索；不提供研究建议、不调整方向，也不在研究员之间分享信息。任一正式结果达标或整组到时即停止。配置与比较方法见 [pass@k](docs/passk.md)。

工作台可在新建研究或启动前选择纯 pass@k 或 pass@k + RSI 协调，协调模式提供 v0.2.1 和 v0.2.2，新研究默认 v0.2.2。RSI 协调作为附加模块，增加原始证据查看、定向共享和非阻塞建议。启动配置和模块版本会冻结；已下线的旧版分工模式仍可查看历史记录，不能启动或恢复。详见[协作配置](docs/collaboration.md)。

研究图中每个 Agent 占一列，进展按实际发生时间向下展开，空白时段压缩；个人有效时间单独显示在研究员卡片中。默认展示成绩突破、汇报、建议和待完成作业，也可切换“只看协作”或展开全部作业。虚线根据建议附带的同一研究内的引用标明信息来源；点击事件可查看原文和送达状态。送达不代表已被采纳，没有明确同伴来源的消息显示为 RSI 的建议。此视图只读取历史记录，不改变 agent 协作或补写实验数据。

`rsi`、`rsi web` 和 `rsi workbench` 均以前台方式启动同一个新工作台；
`python run.py --type web` 负责后台启动或复用服务。新工作台、`rsi research` 和
`rsi preflight` 的数据目录统一按 `--data-root`、`RSI_DATA_ROOT`、`.rsi/research-lab`
的顺序选择。以前直接运行 CLI 保存在 `.rsi` 的研究，可用 `--data-root .rsi` 打开。
研究入口统一为 ResearchApp；旧 `tui/run/resume/status/local-app`、TUI/MCP 控制器和
固定轮次调度器已移除。旧计划与检查点不迁移，请使用当前配置新建研究。

仓库入口自动在后台启动服务；已有兼容的 RSI 工作台则复用，其他进程占用端口则报错，不关闭或覆盖它。用 `--port 8200` 指定端口，用 `--data-root /path/to/data` 指定数据目录。旧版 RSI 服务需要先重启到当前版本。

服务日志和 PID 位于 `<数据目录>/runtime/workbench-<端口>.log / .pid`。若希望前台运行、用 Ctrl+C 停止服务：

```bash
uv run --env-file .env --locked rsi workbench --port 8197
```

## 一键运行 KernelBench pass@k demo

拥有 et15 SSH 和已部署 GPUQ 的访问权限后，无需先打开浏览器或重复填写需求：

```bash
uv run --env-file .env --locked python run.py --type demo
```

脚本自动启动或复用工作台，默认以 v0.2.2 协调模式启动 L3P10 的四名独立研究员，探索限时一小时、提交不限次数。结束后自动冻结最佳候选、独立复测并归档。关闭浏览器或启动终端不影响后台研究。

三题并行、每名研究员半小时有效时间的固定实验，见 [v0.2.1 示例](examples/kernelbench_artifacts/v0.2.1/README.md)。

题目、提交预算、环境检查、恢复命令和历史结果见 [KernelBench demo 快速指南](examples/kernelbench_artifacts/README.md)。这条入口优化具体 kernel，不需要额外 Task Agent；[通用 harness 优化](examples/kernelbench/README.md)是另一种任务。

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

## 数学与 LCB

数学和 LCB 均使用 ResearchApp。操作员适配器准备固定的题目、目标模型和评分身份，
研究模型自主调用候选与验证工具：

- [Arithmetic quickstart](examples/local/README.md)：3 道 TRAIN / 3 道 VALIDATE 原创算术题。
- [LiveCodeBench](examples/remote/README.md)：EnvArena 固定题目与 Reward，研究员修改通用 system prompt。

更多任务、Runtime 配置和部署资产见[示例目录](examples/README.md)。

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

开发检查：`make check`；前端检查：`npm ci --prefix tests/frontend && npm test --prefix tests/frontend`。
