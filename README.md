# AReaL-RSI

AReaL-RSI 是基于 DeepSeek Harness 的本地研究与自迭代工具。用户通过中文对话生成实验计划，确认后由独立 ResearchAgent 研究线持续改进 Harness，并保存评测、版本和恢复检查点。

当前支持本地 Harness 执行、内置算术示例、GSM8K 评测和可选的 Artifact 工作流。默认固定模型，迭代提示词、技能、工具和 MCP 配置。

## 启动

需要 macOS、Python 3.11+、uv，以及 Node.js 22.19+（或 24+）和 pnpm。前台 TUI 和后台研究 worker 共用当前数据目录内的 DSH CLI `0.1.2-rc.1`。Python SDK 负责调用这份 CLI，后台通过 macOS 的 `sandbox-exec` 隔离工作目录。

先准备本机配置文件：

```bash
uv sync --locked
cp -n .env.example .env
```

编辑 `.env`，填写模型服务提供的模型名和服务地址。服务需支持 Chat Completions；`RSI_BASE_URL` 使用 API 根地址（通常以 `/v1` 结尾），不用附加 `/chat/completions`。需要认证时填写本机密钥。

| 配置项 | 用途 |
| --- | --- |
| `RSI_MODEL` | 服务提供的模型名 |
| `RSI_BASE_URL` | 模型 API 根地址 |
| `RSI_API_KEY` | 认证信息；无需认证时留空 |
| `RSI_DATA_ROOT` | 实验数据目录；更换模型时使用新目录 |

首次准备当前数据目录的 DSH CLI、TUI 插件和 RSI preset（需要访问 npm 包仓库）：

```bash
uv run --env-file .env --locked rsi tui --setup-only
```

准备完成后，显式加载配置并启动：

```bash
uv run --env-file .env --locked rsi tui
```

`uv sync` 只安装依赖；`.env` 由 `uv run --env-file` 读取。也可以在同一终端 export 这些变量后直接运行 `uv run --locked rsi tui`。Codex 的 TOML 配置不能直接作为 RSI 配置读取；模型名、服务地址和认证信息需映射到上述字段。

启动终端已经 export 的同名变量会优先于 env 文件。更换配置文件时，可先清除旧值再加载新文件：

```bash
unset RSI_MODEL RSI_BASE_URL RSI_API_KEY RSI_DATA_ROOT RSI_BENCHMARK RSI_RESEARCH_DIRECTIONS
uv run --env-file .env --locked rsi tui
```

模型、评测集和研究线数量由启动配置决定，写入对话目标不会改变它们。三条研究线需要在本机配置中设置含三个字符串的 `RSI_RESEARCH_DIRECTIONS`；轮数通过 `rsi_plan` 指定。更换模型应使用新数据目录，已绑定的基线不会被覆盖。工具返回的计划包含实际模型名和研究线数量，应按这些字段核对。

`.env` 被 Git 忽略，`.env.example` 仅包含空模板。认证信息不写入模型版本或实验配置；模型请求显式设置 `store=false`。

在 DSH TUI 的输入框中直接输入：

```text
请启动一个 1 轮的 Harness 自迭代实验，固定当前模型，优化通用提示词和解题检查流程。
```

DSH 通过本地 MCP 的 `rsi_plan` 生成并展示计划后，输入 `确认`，才会调用 `rsi_start` 提交独立实验 worker。默认评测集是原创小型算术题，包含 3 个 TRAIN 和 3 个 VALIDATE 任务。它用于检查完整流程；候选允许退化，最佳版本可能仍为基线。

实验控制使用自然语言：

| 输入示例 | 行为 |
| --- | --- |
| 查看实验状态 | 查询最近实验的聚合状态 |
| 停止实验 RUN_ID | 请求停止指定后台实验，保留检查点 |
| 恢复实验 RUN_ID | 确认后恢复指定实验，复用已完成的工作 |

空闲时连续按两次 Ctrl+C 退出 TUI；已提交的实验继续运行。对话 Session 与实验 Run 分别管理：

```bash
uv run --env-file .env --locked rsi tui --resume DSH_SESSION_ID
uv run --env-file .env --locked rsi status
uv run --env-file .env --locked rsi resume RUN_ID
```

`rsi tui --resume` 恢复 DSH 对话；`rsi resume` 在当前终端恢复实验。使用原来的数据目录和模型配置。旧 Python 界面的 `/status`、`/stop`、`/resume` 不再是 RSI 的界面命令，DSH 自身命令由插件提供。

统一安装固定版本的 `@deepseek-ai/dsh` CLI 与 `@deepseek-harness-tui/dsh-tui` 插件。DSH 版本由共享安装模块管理，多个 worker 并发启动时通过文件锁复用同一份完整安装。它们位于 `RSI_DATA_ROOT` 的 `dsh-cli/` 和 `dsh-home/`，无需全局安装 DSH。前台选择 TUI profile，后台选择 `sdk-minimal`，两者执行同一份 CLI；后台仅可读取共享安装，不能改写它。Python SDK 的依赖仍会附带预打包运行时，但 RSI 不启动该文件。RSI preset 首次复制 DSH 标准研发工具并设置入口角色，后续启动保留用户对 preset 的修改。上游 TUI 的语言、主题等界面偏好位于用户目录 `~/.dsh-tui`。

## Web UI

另开一个终端，在项目目录加载同一份配置，使用与 TUI 相同的数据目录：

```bash
uv run --env-file .env --locked rsi web --port 8765
```

浏览器打开 [http://127.0.0.1:8765](http://127.0.0.1:8765)，查看研究方向、执行阶段、TRAIN/VALIDATE 得分、版本关系及选版依据。页面自动刷新，只读并绑定本机；无需模型连接配置。

指定单个实验：`uv run --env-file .env --locked rsi web --port 8765 --run RUN_ID`。

## 多研究线与示例

在启动 TUI 前指定研究方向，数量即 ResearchAgent 数量（默认一条）：

```bash
export RSI_RESEARCH_DIRECTIONS='["错误归因与通用提示", "工具使用和验证流程"]'
uv run --env-file .env --locked rsi tui
```

每条线每轮提出一个修改，轮数上限对每条线分别生效；失败和剪枝可能导致实际完成数小于上限。各线只读取 TRAIN 证据，VALIDATE 仅以聚合结果参与选版和剪枝。

- [GSM8K 示例](examples/gsm8k/README.md)：下载固定数据版本并准备训练/留出验证分区。
- [Artifact 示例](examples/artifact/README.md)：显式装配产物研究 Agent、构建器和评测工作流。

## 代码结构

```text
src/areal_rsi/
├── api/                  公共数据对象与协议
├── base/                 本地内容、Memory、轨迹与版本存储
├── modules/              DSH 运行时、构建、Benchmark 与评测
├── agents/
│   ├── rsi/              RSI Agent：对话、计划与研究线剪枝
│   ├── research/         ResearchAgent：Harness / Artifact 提案与选版
│   └── common.py         共用 JSON 解析与格式重试
├── workflows/            Python 迭代、选版、停止和恢复
├── framework/            TrajectoryStore 与 Verifier 扩展框架
├── config.py             本机模型连接配置
├── app.py                本地组件装配
├── ui/                   DSH TUI 入口、MCP、后台 worker 和只读 Web UI
└── default_harness/      初始提示词、技能、工具与 MCP 配置
```

[架构说明](docs/architecture.md) 描述运行与隔离边界；[接口说明](docs/interfaces/README.md) 描述数据对象和扩展协议。

公共 API 定义了 TrajectoryStore 与用户 Verifier 的接入协议。Framework 提供 append-only FileTrajectoryStore、provider factory registry、Python Verifier loader 和统一执行器；具体 Benchmark Verifier、远程 Store 和 Runtime Adapter 由使用者实现并注册。

## 开发检查与数据

```bash
make test   # 离线单元测试，不调用模型或下载数据
make check  # 单元测试、Ruff、Mypy
```

运行记录位于 `RSI_DATA_ROOT`：`content/` 保存按摘要寻址的内容，`versions/` 保存版本，`run-states/` 保存状态，`runs/` 保存逐轮结果，`trajectories/` 保存轨迹索引，`dsh-home/` 保存 DSH 对话，`experiment-plans/` 保存待确认计划，`workers/` 保存后台进程状态和日志，`gateway/` 保存实验模型请求和响应审计。旧 `tui/` 对话记录继续保留。这些运行数据由 Git 忽略；Web UI 只返回聚合状态。
