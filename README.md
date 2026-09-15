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

这里启动原生 **DSH TUI plugin**（`@deepseek-harness-tui/dsh-tui`），保留全屏交互、自然语言对话与 DSH 会话历史。直接说“开始这项研究”，Interaction Agent 会通过 MCP 启动当前配置。

也可以不传配置，直接启动 `rsi tui`，然后自然对话：

> 用内置算术 case 准备一个最多 1 名研究员的数学实验，先展示计划，不要开始。

> 开始这个研究。

> 查看进度。／暂停当前研究。／继续当前研究。

数学可以指定 `gsm8k` 和数据目录；LCB 可以说“用内置 LCB case 准备研究”。MCP 的准备操作不启动后台模型，明确要求开始后才启动。普通聊天不创建研究。

研究仍由统一工作台后台执行，输入 DSH 的 `/exit` 退出前台不会停止研究。同一研究也能在 Web 查看和操作。

```bash
# 绑定已有研究
uv run --env-file .env --locked rsi tui --resume <run_id>
# 恢复原 DSH 对话历史（session ID 在 /exit 时显示）
uv run --env-file .env --locked rsi tui --session <dsh_session_id>
# 可组合 --session 和 --resume，同时恢复对话并绑定研究
```

首次需要 pnpm 安装固定版本 DSH TUI plugin；可先运行 `rsi tui --setup-only`。研究模型使用 `RSI_*` 或已有 `RSI_TEACHER_*` 配置；前台可通过 `RSI_AGENT_MODELS` 的 `interaction-agent` 单独覆盖。没有另一套旧研究模式。

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

`RSI_DATA_ROOT` 默认 `.rsi/research-lab`。会话、任务、报告、作业、候选及验证记录在 `runs/<run_id>/`。不读取旧固定流程的 `run-states/`，不提供旧固定计划迁移；可配置 Task Runtime 继续支持，见 [Runtime 接入](docs/runtime-package.md)。旧数据不会被删除。

新任务通过 `ResearchConfig` 提供执行命令与可信验证器，见[架构](docs/architecture.md)、[接口](docs/interfaces/README.md)和[研究设计](docs/rsi-research-design.md)。远端评测仍保留固定版本、原始回执、取消与重连逻辑。

```bash
make check
npm ci --prefix tests/frontend && npm test --prefix tests/frontend
```

真实验收记录见[数学与 LCB 迁移验收](docs/unified-acceptance.md)。
