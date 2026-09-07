# AReaL-RSI

AReaL-RSI 是基于 DeepSeek Harness 的本地研究与自迭代工具。用户通过中文对话生成实验计划，确认后由独立 ResearchAgent 研究线持续改进 Harness，并保存评测、版本和恢复检查点。

当前支持本地 Harness 执行、内置算术示例、GSM8K 评测和可选的 Artifact 工作流。默认固定模型，迭代提示词、技能、工具和 MCP 配置。

## 启动

需要 macOS、Python 3.11+ 和 uv；本地执行使用 macOS 的 `sandbox-exec` 隔离工作目录。DSH 二进制随 Python SDK 安装。

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

然后显式加载配置并启动：

```bash
uv run --env-file .env --locked rsi tui
```

`uv sync` 只安装依赖；`.env` 由 `uv run --env-file` 读取。也可以在同一终端 export 这些变量后直接运行 `uv run --locked rsi tui`。Codex 的 TOML 配置不能直接作为 RSI 配置读取；模型名、服务地址和认证信息需映射到上述字段。

`.env` 被 Git 忽略，`.env.example` 仅包含空模板。认证信息不写入模型版本或实验配置；模型请求显式设置 `store=false`。

在 `你>` 提示符后输入：

```text
请启动一个 1 轮的 Harness 自迭代实验，固定当前模型，优化通用提示词和解题检查流程。
```

看到实验计划后输入 `确认`。默认评测集是原创小型算术题，包含 3 个 TRAIN 和 3 个 VALIDATE 任务。它用于检查完整流程；候选允许退化，最佳版本可能仍为基线。

| 输入 | 行为 |
| --- | --- |
| `/status` | 查看实验状态和完成轮数 |
| `/stop` | 停止当前实验，保留检查点 |
| `/resume RUN_ID` | 恢复指定实验，复用已完成的工作 |
| `/exit` | 退出对话；运行中的实验需要先停止 |

也可以在终端使用 `uv run --env-file .env --locked rsi status` 查询实验。访问已有实验时，保持相同的 `RSI_DATA_ROOT` 和模型配置。

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
├── config.py             本机模型连接配置
├── app.py                本地组件装配
├── ui/                   TUI、CLI 和只读 Web UI
└── default_harness/      初始提示词、技能、工具与 MCP 配置
```

[架构说明](docs/architecture.md) 描述运行与隔离边界；[接口说明](docs/interfaces/README.md) 描述数据对象和扩展协议。

## 开发检查与数据

```bash
make test   # 离线单元测试，不调用模型或下载数据
make check  # 单元测试、Ruff、Mypy
```

运行记录位于 `RSI_DATA_ROOT`：`content/` 保存按摘要寻址的内容，`versions/` 保存版本，`run-states/` 保存状态，`runs/` 保存逐轮结果，`trajectories/` 保存轨迹索引，`tui/` 保存对话，`gateway/` 保存模型请求和响应审计。这些运行数据由 Git 忽略；Web UI 只返回聚合状态。
