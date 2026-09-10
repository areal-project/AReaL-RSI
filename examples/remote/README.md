# 最小 LCB remote 测试

直接打开 DSH TUI：

```bash
uv run --env-file .env --locked rsi tui
```

说“用 remote LCB demo 生成一个 1 轮的计划”，或在对话中指定 `examples/remote/livecodebench.yaml`。
mode 从 YAML 读取；同一会话可以继续选择 local demo，不需要重启。核对计划并确认后才执行。
本配置的 `research_harness: dsh` 使用 ARealAgentRuntime 的 DSH，
优先于旧 Runtime JSON 的研究角色配置。以下脚本也使用同一份 YAML。

`livecodebench.yaml` 固定 Benchmark `rsi-lcb-claude-code-smoke@1.0.5`、
其绑定的 Reward `rsi-lcb-claude-code-reward@1.0.5`、
Model `qwen-3-8-vlm-judge@1.0.1-gameagent-system-normalize` 和
Harness `rsi-lcb-claude-code@1.0.5`。

2727 用于 TRAIN，2777 用于 VALIDATE，每题由原 LCB 评分脚本执行 14 个测试。
Arena 的 Claude Code 负责解题，本地 ResearchAgent 只修改远端 system prompt 配置。
这个示例选择 `envarena` backend；remote 模式也可注册其他实现。

在 `.env` 中配置本地研究模型的 `RSI_TEACHER_MODEL`、`RSI_TEACHER_BASE_URL` 和
`RSI_TEACHER_API_KEY`。Arena 认证使用 `ARENA_OPENAPI_TOKEN` 或已配置的
`~/.envarena/config.toml`。本地 runtime 的其他选项见 [Runtime 配置](../../docs/runtime-package.md)。

生成计划：

```bash
export RSI_RUNTIME_CONFIG="$PWD/examples/runtime/dsh.json"
export RSI_REMOTE_BACKEND=envarena
export RSI_RESEARCH_AGENT_COUNT=1
uv run --env-file .env --locked python examples/remote/run.py \
  --config examples/remote/livecodebench.yaml \
  --root .rsi/lcb-smoke --iterations 1
```

确认计划后，在同一命令末尾添加 `--launch`，执行一轮：
基线 VALIDATE → TRAIN → 提案 → 发布候选配置 → 候选 VALIDATE → 选版。

恢复已有实验时使用相同配置和数据目录：

```bash
export RSI_REMOTE_CONFIG="$PWD/examples/remote/livecodebench.yaml"
export RSI_DATA_ROOT="$PWD/.rsi/lcb-smoke"
uv run --env-file .env --locked rsi resume RUN_ID
```

`envarena` backend 仍兼容旧的 `RSI_ARENA_CONFIG`；设置 `RSI_REMOTE_CONFIG` 时优先使用新变量。

远端输出按 `artifact_globs` 收集，默认规则为 `*.py`、`*.js`、`response.txt` 和
`runner_result.json`。可在 YAML 中改为所需的相对文件名规则，例如
`artifact_globs: ["*.js", "summary.txt"]`；空列表表示不下载产物。
仅下载当前 attempt、`harness` 来源、不超过 2 MiB 的匹配文件，不下载 Reward 文件。
这些产物、轨迹和验证结果以独立证据文件交给 ResearchAgent，工具按授权索引分段读取。
改变收集规则需要使用新的实验数据目录，以免复用不完整的旧证据。

`research_harness` 选择与 Runtime JSON 相同的后端时，保留该角色已有的配置
（没有角色配置时保留 default 配置）；切换后端时不继承不兼容的选项。
发布或复用 RSI 创建的远端版本前，会比较完整 spec，而不只信任摘要标签。

## Terminal-Bench 2.1

[`tb21.yaml`](tb21.yaml) 演示 `env_refs` 类型的 TB2.1 Benchmark。它固定
`tb21-terminus2-full@2.1.1`、`tb2-reward@4.0.16` 和
`gpt-5-6-sol-4ipic@1.0.0`，从 `terminus-2-tb21@1.0.12` 开始演化。
TRAIN 与 VALIDATE 明确隔离；允许 ResearchAgent 修改通用 system prompt 以及 Terminus
白名单推理参数，但 ModelVersion 和模型权重始终不变。

四条独立研究线、每线五轮可并行完成 20 个框架迭代：

```bash
export RSI_RUNTIME_CONFIG="$PWD/examples/runtime/dsh.json"
export RSI_REMOTE_BACKEND=envarena
export RSI_RESEARCH_AGENT_COUNT=4
uv run --env-file .env --locked python examples/remote/run.py \
  --config examples/remote/tb21.yaml --root .rsi/areal-tb21 \
  --iterations 5 --launch
```

这个筛选实验的 VALIDATE 分数不等于完整 TB2.1 分数；最佳候选必须再跑原始 89 题
Benchmark，才能声明相对完整 baseline 的提升。
