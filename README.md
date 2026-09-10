# AReaL-RSI

AReaL-RSI 是一个按 run 归档的多 Agent 研究系统。RSI Agent 自主调研和组织研究方向，Research Agents 保持连续会话，完成任务后具体汇报并等待下一条指令。用户通过本地工作台查看进度和控制研究。

新开放研究入口提供 KernelBench harness 优化和 CUHK-X 模型训练两个示例。原有固定工作流继续支持本地 Harness、内置算术、GSM8K 和 Artifact；该入口的 Interaction Agent、RSI Agent 和 ResearchAgent 组可以分别配置模型，默认使用 teacher。所有 ResearchAgent 共用一个模型。Task Agent 直接使用 target，不存在另一份 Task Agent 模型配置。当前阶段固定 target 的模型参数，迭代其提示词、技能、工具和 MCP 配置。

## 开放式 Research 工作台

新的多 Agent 入口支持连续会话、RSI 自主规划、报告后等待、后台作业、run 内共享证据和可交互搜索图。先配置 `.env` 的 `RSI_BASE_URL / RSI_API_KEY / RSI_MODEL / RSI_REASONING_EFFORT`，然后运行：

```bash
uv sync --locked
uv run --env-file .env --locked rsi workbench
```

打开 [本地工作台](http://127.0.0.1:8766)，直接向 Interaction 描述研究目标和环境。它会逐步整理需求与执行条件，确认后交给 RSI；无需选模板或填写参数表。任务可以没有起点、GPU 或分数，已有 KernelBench、CUHK-X 适配器是可选的接入便利工具。环境接入、终端运行和恢复方法见[工作台使用说明](docs/research-workbench.md)，角色与流程见[设计说明](docs/rsi-research-design.md)，当前实现取舍见[精简设计](docs/research-simplification.md)，已跑通的链路与实际限制见[验收记录](docs/research-acceptance.md)。

新入口通过 Responses API 运行研究模型，默认 `gpt-6-astra / xhigh`；下面的 teacher/target、TUI 与固定 evolution 命令属于原有入口，其配置继续兼容。

## 启动

旧入口和 Task runtime 需要 macOS、Python 3.11+、uv，以及 Node.js 22.19+（或 24+）和 pnpm；它们使用数据目录内的 DSH CLI `0.1.2-rc.1`，后台通过 `sandbox-exec` 隔离工作目录。新工作台的三个研究角色使用 Responses 连续会话，共享 DSH 基础执行工具和联网工具；工具插件通过 npm 按 lockfile 安装，不需要全局 DSH CLI。详情见[基础工具说明](docs/interfaces/dsh-tools.md)。

先准备本机配置文件：

```bash
uv sync --locked
cp -n .env.example .env
```

编辑 `.env`，分别填写 teacher 和 target 两组连接。两组服务均需支持 Chat Completions；`BASE_URL` 使用 API 根地址（通常以 `/v1` 结尾），不用附加 `/chat/completions`。

| 配置项 | 用途 |
| --- | --- |
| `RSI_TEACHER_MODEL` / `RSI_TEACHER_BASE_URL` | Interaction Agent、RSI Agent 和 ResearchAgent 使用的模型及服务 |
| `RSI_TEACHER_API_KEY` | teacher 服务认证信息；无需认证时留空 |
| `RSI_TARGET_MODEL` / `RSI_TARGET_BASE_URL` | 待优化的目标模型及服务，供 Task Agent 和评测使用 |
| `RSI_TARGET_API_KEY` | target 服务认证信息；无需认证时留空，不继承 teacher 密钥 |
| `RSI_DATA_ROOT` | 共享实验数据目录，可保存多个模型的实验 |

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

以上模型名和地址都是占位示例。也可将 JSON 写入本地 env 文件的 `RSI_AGENT_MODELS`。覆盖项的含义是：

| Agent ID | 默认值与覆盖规则 |
| --- | --- |
| `interaction-agent` | Interaction Agent 对话模型，默认 teacher；不参与后台 Run 的恢复校验 |
| `rsi-agent` | RSI Agent 的模型，默认 teacher |
| `research-agent` | 所有 ResearchAgent 共用的覆盖，优先于 teacher |

只覆盖 `model` 时继承 teacher 服务及认证；更换 `base_url` 后默认不继承原服务密钥。需要认证时，通过 `api_key_env` 引用本机环境变量，例如 `RESEARCH_API_KEY`，其值不会进入生成配置或计划。该变量必须存在，也可以显式设置为空以关闭认证。Agent 覆盖只接受 `model`、`base_url`、`api_key_env` 三项。不接受 `task-agent` 或 `research-agent-1`、`research-agent-2` 等单条研究线覆盖；Task Agent 始终使用 `RSI_TARGET_*`。

未配置覆盖时，当前默认行为保持不变。RSI Agent 和 ResearchAgent 都有完整覆盖时可以不配置 teacher；target 始终通过 `RSI_TARGET_*` 配置。

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
unset RSI_MODEL RSI_BASE_URL RSI_API_KEY
unset RSI_TEACHER_MODEL RSI_TEACHER_BASE_URL RSI_TEACHER_API_KEY
unset RSI_TARGET_MODEL RSI_TARGET_BASE_URL RSI_TARGET_API_KEY
unset RSI_DATA_ROOT RSI_BENCHMARK RSI_RESEARCH_AGENT_COUNT
uv run --env-file .env --locked rsi tui
```

各类 Agent 的有效模型、评测集和研究线数量由启动配置决定，写入对话目标不会改变它们。三条研究线设置 `RSI_RESEARCH_AGENT_COUNT=3`；轮数通过 `rsi_plan` 指定。所有 ResearchAgent 接收相同的实验目标并使用同一个模型，但保留独立 Session、workspace、Memory 查询、迭代状态和版本分支。仅改变 Interaction Agent 或未被使用的默认值不会影响后台恢复；后台模型覆盖变化会按角色校验。目标基线只由有效 Task 模型及初始 Harness 决定，同一目录可保存多组配置，不覆盖旧实验。

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

统一安装固定版本的 `@deepseek-ai/dsh` CLI 与 [`@deepseek-harness-tui/dsh-tui`](https://github.com/ccch1mneyyy/dsh-TUI) 插件。插件包名、版本和 profile 位于 `modules/dsh_profile.py`；静态 Cordis patch 与 Persona 位于 `resources/dsh/`；`modules/dsh_config.py` 只填入模型、数据目录、workspace 和 MCP 环境变量名。DSH 版本由共享安装模块管理，多个 worker 并发启动时通过文件锁复用同一份完整安装。它们位于 `RSI_DATA_ROOT` 的 `dsh-cli/` 和 `dsh-home/`，无需全局安装 DSH。前台选择 TUI profile，后台选择 `sdk-minimal`，两者执行同一份 CLI；后台仅可读取共享安装，不能改写它。Python SDK 的依赖仍会附带预打包运行时，但 RSI 不启动该文件。RSI preset 首次复制 DSH 标准研发工具；每次启动从 `resources/dsh/persona.md` 同步 Interaction Agent 角色定义，并保留 preset 中的工具等其他配置。角色提示修改后需重启 TUI；新对话会使用当前定义。上游 TUI 的语言、主题等界面偏好位于用户目录 `~/.dsh-tui`。

## Web UI

另开一个终端，在项目目录加载同一份配置，使用与 TUI 相同的数据目录：

```bash
uv run --env-file .env --locked rsi web --port 8765
```

浏览器打开 [http://127.0.0.1:8765](http://127.0.0.1:8765)，查看独立研究线、执行阶段、TRAIN/VALIDATE 得分、版本关系及选版依据。页面自动刷新，只读并绑定本机；无需模型连接配置。

指定单个实验：`uv run --env-file .env --locked rsi web --port 8765 --run RUN_ID`。

## 多研究线与示例

在启动 TUI 前指定 ResearchAgent 数量（默认一条）：

```bash
export RSI_RESEARCH_AGENT_COUNT=3
uv run --env-file .env --locked rsi tui
```

每条线每轮提出一个修改，所有研究线使用同一实验目标并独立分析证据、生成候选。轮数上限对每条线分别生效；失败和剪枝可能导致实际完成数小于上限。各线只读取 TRAIN 证据，VALIDATE 仅以聚合结果参与选版和剪枝。

- [GSM8K 示例](examples/gsm8k/README.md)：下载固定数据版本并准备训练/留出验证分区。
- [Artifact 示例](examples/artifact/README.md)：显式装配产物研究 Agent、构建器和评测工作流。

## 代码结构

```text
src/areal_rsi/
├── api/                  公共数据对象与协议
├── base/                 本地内容、Memory、轨迹与版本存储
├── modules/              Responses / DSH 运行时、构建、Benchmark 与评测
├── coordination/         持久消息、研究节点、工具、预算与调度
├── execution/            后台作业、跨 run 资源分配与恢复
├── adapters/             KernelBench / CUHK-X 数据与验证适配
├── agents/
│   ├── interaction/      用户需求与进展解释
│   ├── rsi/              RSI Agent：调研、计划与研究协调
│   ├── research/         研究方向角色，以及旧 Harness / Artifact 适配
│   └── common.py         共用 JSON 解析与格式重试
├── workflows/            Python 迭代、选版、停止和恢复
├── framework/            TrajectoryStore 与 Verifier 扩展框架
├── config.py             本机模型连接配置
├── app.py                旧固定工作流组件装配
├── research_app.py       开放式 Research 工作台组件装配
├── resources/dsh/        TUI Cordis patch 模板与 RSI Persona
├── ui/                   Research 工作台、CLI，以及旧 TUI / MCP / Web 入口
└── default_harness/      初始提示词、技能、工具与 MCP 配置
```

[架构说明](docs/architecture.md) 描述运行与隔离边界；[接口说明](docs/interfaces/README.md) 描述数据对象和扩展协议。

[RSI Agent 与 ResearchAgent 协作设计](docs/rsi-research-design.md) 描述目标流程，涵盖研究调度、报告、上下文恢复、评测隔离和 run 数据管理。当前已落地 run 数据归属与通用节点定义，协作运行时按后续 PR 推进。

[内部实现参考](docs/rsi-research-implementation.md) 保留完整设计、运行时接点、结构化进度与分阶段 PR 计划。

公共 API 定义了 TrajectoryStore 与用户 Verifier 的接入协议。Framework 提供 append-only FileTrajectoryStore、provider factory registry、Python Verifier loader 和统一执行器；具体 Benchmark Verifier、远程 Store 和 Runtime Adapter 由使用者实现并注册。

## 开发检查与数据

```bash
make test   # 离线单元测试，不调用模型或下载数据
make check  # 单元测试、Ruff、Mypy
```

新实验的研究数据集中在 `RSI_DATA_ROOT/runs/<run_id>/`：

```text
run.json                         计划、可空起点、运行状态、最佳版本引用、研究 Agent ID
agents/<agent_id>/agent.json      ResearchAgent 状态、当前节点、迭代 ID 列表
agents/<agent_id>/sessions/       本 run 的 Agent 会话文件
agents/<agent_id>/workspace/      ResearchAgent 工作区
iterations/<iteration_id>.json   通用研究节点：任务、状态、报告、材料引用
memory/                          仅本 run 共享的经验
trajectories/                    本 run 的轨迹索引
workflow/                        当前工作流的基线评测、逐轮结果和恢复检查点
runtime/                         worker 状态、锁、日志、请求审计、构建与执行临时目录
```

`FileRunStore` / `RunPaths` 统一管理读写位置；状态、Agent、节点分别保存，父记录通过 ID 关联。ResearchIteration 不要求产生版本或评测结果，失败和取消的尝试也会保存。起点与最佳版本在数据模型中可以为空；当前 Harness / Artifact 工作流仍要求起点，首次有效评测后才设置最佳版本。

**EvolutionResult 是当前工作流使用的一种可选结果格式，不是通用节点的必需项。** 它位于 workflow 层，用来承接现有 Harness / Artifact 流程的修改、候选和评测字段，通过节点的 `report_ref` 引用。当前这两个工作流用它保存完成结果和恢复检查点；其他工作流可以完全不用它，节点可以引用自己的报告和材料，不必填充空的 EvolutionResult。`SolutionVersion` 表示可执行版本，EvolutionResult 只通过 ID 引用输入、候选和下一版本，两者不是同一类对象。具体关系见[接口说明](docs/interfaces/README.md#researchiteration-与工作流结果)。

根目录保留共享设施：`content/` 和 `versions/` 存不可变正文与版本，`model-index/`、`baselines/` 是模型和初始 Harness 版本的去重索引，`dsh-cli/`、`dsh-home/` 保存共享安装与前台会话，`experiment-plans/` 保存启动前计划，`workers/` 保留跨 run 控制锁与计划到 run 的索引。程序化调用产生的运行前对话放在 `conversations/`。各 run 独立保存自己的起点引用与基线评测，Memory 不跨 run 自动共享。

旧 `run-states/` 记录仍可查看和恢复；后续保存时写入新状态结构，保留旧源记录、执行锁和检查点位置。根目录历史 Memory 不自动导入，也不删除。内容和版本使用共享引用，因此完整归档还需收集引用的正文；这里只调整归属，未实现归档导出工具。这些运行数据由 Git 忽略，Web UI 只返回聚合状态。
