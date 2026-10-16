# AReaL-RSI

AReaL-RSI 是基于 DeepSeek Harness 的本地研究与自迭代工具。用户通过中文对话生成实验计划，确认后由独立 ResearchAgent 研究线持续改进 Harness，并保存评测、版本和恢复检查点。

后台 RSI Agent、ResearchAgent、Task Agent 已通过 ARealAgentRuntime Python package 执行，
支持配置其现有 Harness；前台保留 DSH TUI。快速了解见 [接入简要说明](docs/runtime-quickstart.md)，完整配置和能力边界见 [Runtime 包接入指南](docs/runtime-package.md)。

当前支持本地 Harness 执行、内置算术示例、GSM8K 评测和可选的 Artifact 工作流。Interaction Agent、RSI Agent 和 ResearchAgent 组可以分别配置模型，默认使用 teacher。所有 ResearchAgent 共用一个模型。Task Agent 直接使用 target，不存在另一份 Task Agent 模型配置。当前阶段固定 target 的模型参数，迭代其提示词、技能、工具和 MCP 配置。

## 启动

需要 macOS、Python 3.11+、uv，以及 Node.js 22.19+（或 24+）和 pnpm。选择 DSH 时，前台 TUI 和后台研究 worker 共用当前数据目录内的 DSH CLI `0.1.2-rc.1`。Python SDK 负责调用这份 CLI，后台通过 macOS 的 `sandbox-exec` 隔离工作目录。

先准备本机配置文件：

```bash
uv sync --locked
uv run --locked rsi config init
```

编辑 `~/.areal_rsi/config.json`，分别填写 teacher 和 target 两组连接。两组服务均需支持 Chat Completions；`BASE_URL` 使用 API 根地址（通常以 `/v1` 结尾），不用附加 `/chat/completions`。

| 配置项 | 用途 |
| --- | --- |
| `RSI_TEACHER_MODEL` / `RSI_TEACHER_BASE_URL` | Interaction Agent、RSI Agent 和 ResearchAgent 使用的模型及服务 |
| `RSI_TEACHER_API_KEY` | teacher 服务认证信息；无需认证时留空 |
| `RSI_TARGET_MODEL` / `RSI_TARGET_BASE_URL` | 待优化的目标模型及服务，供 Task Agent 和评测使用 |
| `RSI_TARGET_API_KEY` | target 服务认证信息；无需认证时留空，不继承 teacher 密钥 |
| `RSI_DATA_ROOT` | 共享实验数据目录，可保存多个模型的实验 |

配置文件示例（将模型名、地址和密钥替换为自己的值）：

```json
{
  "RSI_TEACHER_MODEL": "teacher-model",
  "RSI_TEACHER_BASE_URL": "https://teacher.example/v1",
  "RSI_TEACHER_API_KEY": "",
  "RSI_TARGET_MODEL": "target-model",
  "RSI_TARGET_BASE_URL": "https://target.example/v1",
  "RSI_TARGET_API_KEY": "",
  "RSI_DATA_ROOT": ".rsi/local",
  "RSI_BENCHMARK": "arithmetic-v1",
  "RSI_RESEARCH_AGENT_COUNT": 1,
  "RSI_AGENT_MODELS": {}
}
```

额外认证变量（例如 Agent 覆盖引用的 `RESEARCH_API_KEY`）也可以作为顶层字符串字段填写。

Teacher 与 target 是默认连接，可以来自不同服务。旧的 `RSI_MODEL`、`RSI_BASE_URL`、`RSI_API_KEY` 仅作为 teacher 的兼容配置；target 必须显式配置。

可以用 `RSI_AGENT_MODELS` 为特定 Agent 覆盖默认值。例如，在当前终端设置：

```bash
export RSI_AGENT_MODELS='{
  "interaction-agent": {"model": "interaction-agent-model"},
  "rsi-agent": {"model": "rsi-agent-model"},
  "research-agent": {
    "model": "research-agent-model",
    "base_url": "http://127.0.0.1:9000/v1",
    "api_key_env": "RESEARCH_API_KEY"
  }
}'
```

以上模型名和地址都是占位示例。也可将 JSON 写入 `~/.areal_rsi/config.json` 的 `RSI_AGENT_MODELS`（直接使用 JSON 对象）。覆盖项的含义是：

| Agent ID | 默认值与覆盖规则 |
| --- | --- |
| `interaction-agent` | Interaction Agent 对话模型，默认 teacher；不参与后台 Run 的恢复校验 |
| `rsi-agent` | RSI Agent 的模型，默认 teacher |
| `research-agent` | 所有 ResearchAgent 共用的覆盖，优先于 teacher |

只覆盖 `model` 时继承 teacher 服务及认证；更换 `base_url` 后默认不继承原服务密钥。需要认证时，通过 `api_key_env` 引用本机环境变量，例如 `RESEARCH_API_KEY`，其值不会进入生成配置或计划。该变量必须存在，也可以显式设置为空以关闭认证。Agent 覆盖只接受 `model`、`base_url`、`api_key_env` 三项。不接受 `task-agent` 或 `research-agent-1`、`research-agent-2` 等单条研究线覆盖；Task Agent 始终使用 `RSI_TARGET_*`。

未配置覆盖时，当前默认行为保持不变。RSI Agent 和 ResearchAgent 都有完整覆盖时可以不配置 teacher；target 始终通过 `RSI_TARGET_*` 配置。

首次准备当前数据目录的 DSH CLI、TUI 插件和 RSI preset（需要访问 npm 包仓库）：

```bash
uv run --locked rsi tui --setup-only
```

准备完成后启动（自动读取配置）：

```bash
uv run --locked rsi tui
```

所有 `rsi` 命令默认读取 `~/.areal_rsi/config.json`，无需 `--env-file`。配置键沿用上表的 `RSI_*` 名称，值使用字符串或数字；`RSI_AGENT_MODELS` 可以直接使用 JSON 对象。显式环境变量优先（包括空值）。文件不存在时仍可仅使用环境变量；文件格式错误会明确报错。`rsi config init` 不覆盖已有文件，新文件权限为 `0600`。相对路径仍以启动目录为准。Python API 调用方可先调用 `areal_rsi.config.load_user_config()`。

启动终端已经 export 的同名变量会优先于 JSON 文件。希望使用文件中的配置时，可先清除旧的环境变量：

```bash
unset RSI_MODEL RSI_BASE_URL RSI_API_KEY
unset RSI_TEACHER_MODEL RSI_TEACHER_BASE_URL RSI_TEACHER_API_KEY
unset RSI_TARGET_MODEL RSI_TARGET_BASE_URL RSI_TARGET_API_KEY
unset RSI_DATA_ROOT RSI_BENCHMARK RSI_RESEARCH_AGENT_COUNT
uv run --locked rsi tui
```

各类 Agent 的有效模型、评测集和研究线数量由启动配置决定，写入对话目标不会改变它们。三条研究线设置 `RSI_RESEARCH_AGENT_COUNT=3`；轮数通过 `rsi_plan` 指定。所有 ResearchAgent 接收相同的实验目标并使用同一个模型，但保留独立 Session、workspace、Memory 查询、迭代状态和版本分支。仅改变 Interaction Agent 或未被使用的默认值不会影响后台恢复；后台模型覆盖变化会按角色校验。目标基线只由有效 Task 模型及初始 Harness 决定，同一目录可保存多组配置，不覆盖旧实验。

个人配置保存在仓库外的 `~/.areal_rsi/config.json`。认证信息不写入模型版本或实验配置；模型请求显式设置 `store=false`。

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
uv run --locked rsi tui --resume DSH_SESSION_ID
uv run --locked rsi status
uv run --locked rsi resume RUN_ID
```

`rsi tui --resume` 恢复 DSH 对话；`rsi resume` 在当前终端恢复实验。使用原来的数据目录和模型配置。旧 Python 界面的 `/status`、`/stop`、`/resume` 不再是 RSI 的界面命令，DSH 自身命令由插件提供。

统一安装固定版本的 `@deepseek-ai/dsh` CLI 与 [`@deepseek-harness-tui/dsh-tui`](https://github.com/ccch1mneyyy/dsh-TUI) 插件。插件包名、版本和 profile 位于 `modules/dsh_profile.py`；静态 Cordis patch 与 Persona 位于 `resources/dsh/`；`modules/dsh_config.py` 只填入模型、数据目录、workspace 和 MCP 环境变量名。DSH 版本由共享安装模块管理，多个 worker 并发启动时通过文件锁复用同一份完整安装。它们位于 `RSI_DATA_ROOT` 的 `dsh-cli/` 和 `dsh-home/`，无需全局安装 DSH。前台选择 TUI profile，DSH 后台使用带 Runtime bundle 的 `agent-runtime` profile，两者默认执行同一份 CLI；后台仅可读取共享安装，不能改写它。Python SDK 的依赖仍会附带预打包运行时，但 RSI 不启动该文件。RSI preset 首次复制 DSH 标准研发工具；每次启动从 `resources/dsh/persona.md` 同步 Interaction Agent 角色定义，并保留 preset 中的工具等其他配置。角色提示修改后需重启 TUI；新对话会使用当前定义。上游 TUI 的语言、主题等界面偏好位于用户目录 `~/.dsh-tui`。

## Web UI

另开一个终端，在项目目录加载同一份配置，使用与 TUI 相同的数据目录：

```bash
uv run --locked rsi web --port 8765
```

浏览器打开 [http://127.0.0.1:8765](http://127.0.0.1:8765)，查看独立研究线、执行阶段、TRAIN/VALIDATE 得分、版本关系及选版依据。页面自动刷新，只读并绑定本机；无需模型连接配置。

指定单个实验：`uv run --locked rsi web --port 8765 --run RUN_ID`。

## 多研究线与示例

在启动 TUI 前指定 ResearchAgent 数量（默认一条）：

```bash
export RSI_RESEARCH_AGENT_COUNT=3
uv run --locked rsi tui
```

每条线每轮提出一个修改，所有研究线使用同一实验目标并独立分析证据、生成候选。轮数上限对每条线分别生效；失败和剪枝可能导致实际完成数小于上限。各线只读取 TRAIN 证据，VALIDATE 仅以聚合结果参与选版和剪枝。

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
├── modules/              DSH 安装、TUI profile、动态 patch 与基础模块实现
├── resources/dsh/        TUI Cordis patch 模板与 RSI Persona
├── ui/                   薄 TUI 启动器、MCP、后台 worker 和只读 Web UI
└── default_harness/      初始提示词、技能、工具与 MCP 配置
```

[架构说明](docs/architecture.md) 描述运行与隔离边界；[接口说明](docs/interfaces/README.md) 描述数据对象和扩展协议。

公共 API 定义了 TrajectoryStore 与用户 Verifier 的接入协议。Framework 提供 append-only FileTrajectoryStore、provider factory registry、Python Verifier loader 和统一执行器；具体 Benchmark Verifier、远程 Store 和 Runtime Adapter 由使用者实现并注册。

## 开发检查与数据

```bash
make test   # 离线单元测试，不调用模型或下载数据
make check  # 单元测试、Ruff、Mypy
```

运行记录位于 `RSI_DATA_ROOT`：`content/` 保存按摘要寻址的内容，`versions/` 保存版本，`model-index/` 复用模型描述，`baselines/` 按目标模型与初始 Harness 快照保存基线，`run-states/` 保存状态，`runs/` 保存逐轮结果，`trajectories/` 保存轨迹索引，`dsh-home/` 保存 DSH 对话，`experiment-plans/` 保存待确认计划，`workers/` 保存后台进程状态和日志，`gateway/` 保存实验模型请求和响应审计。旧 `tui/` 对话记录继续保留。这些运行数据由 Git 忽略；Web UI 只返回聚合状态。

持续生产目标可使用 `rsi exec --spec goal.json --yes --json`，TUI 使用相同 Goal 服务。
详见 [Goal 模式与配置](docs/goal-mode.md) 和 [真实双 Target demo](examples/goals/README.md)。
