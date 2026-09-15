# AReaL-RSI

持续多 Agent 研究：Interaction 与用户确认需求，RSI 制定计划并协调研究员，研究员执行实验、汇报，并在原会话中继续工作。TUI、Web 和 CLI 共用 ResearchApp / ResearchEngine；没有另一套固定迭代模式。

## 安装与配置

需要 Python 3.11+、uv、Node.js 22.19+ 和 npm。数学及 KernelBench Harness 的 Task Agent 还需要 macOS `sandbox-exec` 和 pnpm。LCB 任务在 EnvArena 执行。

```bash
uv sync --locked
cp -n .env.example .env
```

在 `.env` 配置研究模型的 `RSI_BASE_URL / RSI_API_KEY / RSI_MODEL`，接口须支持 Responses API 与工具调用。数学的被测模型另外使用 `RSI_TARGET_BASE_URL / RSI_TARGET_API_KEY / RSI_TARGET_MODEL`。两者可以是同一个模型，也可以不同。LCB 被测模型由远端配置固定。

## 数学与 LCB 实验

先生成可信研究配置，再选择 TUI、Web 或 CLI 启动。准备配置不会启动模型或评测作业；每次准备使用新的输出目录。

```bash
# 最小原创算术：3 道训练题、3 道验证题
uv run --env-file .env --locked rsi prepare --case arithmetic \
  --output .rsi/configs/arithmetic/research.json

# GSM8K：下载固定版本官方数据；已有数据无需再次下载
uv run --locked python -m areal_rsi.modules.gsm8k --directory .rsi/datasets/gsm8k
uv run --env-file .env --locked rsi prepare --case gsm8k \
  --dataset .rsi/datasets/gsm8k --limit 2 \
  --output .rsi/configs/gsm8k/research.json

# LiveCodeBench：先确认 arena doctor 成功
uv run --env-file .env --locked rsi prepare --case lcb \
  --remote-config examples/remote/livecodebench.yaml \
  --output .rsi/configs/lcb/research.json
```

### TUI

```bash
uv run --env-file .env --locked rsi tui --config .rsi/configs/gsm8k/research.json
# LCB 换成 .rsi/configs/lcb/research.json
```

进入终端后输入 `/start` 开始研究。直接输入文字与 Agent 对话；`/list` 列出研究，`/use ID` 切换，`/pause`、`/resume`、`/stop` 控制当前研究，`/quit` 退出终端。PgUp/PgDn 滚动历史。

TUI 自动启动或复用本地工作台服务，退出终端不会停止后台研究。同一研究也能在浏览器查看和操作。再次打开：

```bash
uv run --env-file .env --locked rsi tui --resume <run_id>
```

`--resume` 选择原研究；若研究已暂停，再输入 `/resume`。新建空白对话用 `rsi tui`，已有配置也可在终端输入 `/new /absolute/path/research.json`。

### Web

```bash
uv run --env-file .env --locked rsi workbench --config .rsi/configs/gsm8k/research.json
```

打开 **http://127.0.0.1:8197**，新建研究并开始。TUI 与 Web 使用同一个数据目录、同一个端口即可共同操作。前台服务用 Ctrl+C 停止；希望后台启动时可用：

```bash
uv run --env-file .env --locked python run.py --type web
```

### CLI

```bash
uv run --env-file .env --locked rsi research --config .rsi/configs/gsm8k/research.json
uv run --env-file .env --locked rsi research --config .rsi/configs/lcb/research.json
uv run --env-file .env --locked rsi research --resume <run_id>
```

CLI 直接创建并启动研究，持续输出进度。Ctrl+C 保存会话；CLI 持有执行权时，Web/TUI 对该研究只读。需要多界面同时操作时，用 TUI/Web 的后台服务执行。

这些示例进行一次小规模真实 Harness 改进与对比：baseline → 研究员运行训练题并修改 `prompt.md` → 候选 search 评测 → 选定候选 final 复测。此顺序由任务要求指定，核心引擎仍支持开放研究。final 复用同一留出题池，仅代表独立执行复测，不代表新测试集成绩。GSM8K 不使用官方 test；不保证候选分数提升。

## KernelBench

直接优化八题 kernel 的 demo 需要 et15 SSH 和预部署 GPUQ：

```bash
uv run --env-file .env --locked python run.py --type demo --check
uv run --env-file .env --locked python run.py --type demo
```

详见 [KernelBench demo](examples/kernelbench_artifacts/README.md)；[Harness 优化](examples/kernelbench/README.md)则通过 Task Agent 生成 kernel。两者都使用同一个研究引擎。

## 数据与扩展

`RSI_DATA_ROOT` 默认 `.rsi/research-lab`。会话、任务、报告、作业、候选及验证记录在 `runs/<run_id>/`。不读取旧固定流程的 `run-states/`，不提供旧计划、DSH 会话或旧 Runtime 配置迁移。旧数据不会被删除。

新任务通过 `ResearchConfig` 提供执行命令与可信验证器，见[架构](docs/architecture.md)、[接口](docs/interfaces/README.md)和[研究设计](docs/rsi-research-design.md)。远端评测仍保留固定版本、原始回执、取消与重连逻辑。

```bash
make check
npm ci --prefix tests/frontend && npm test --prefix tests/frontend
```

真实验收记录见[数学与 LCB 迁移验收](docs/unified-acceptance.md)。
