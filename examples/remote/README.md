# 最小 LCB remote 测试

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
export RSI_RUNTIME_CONFIG="$PWD/examples/runtime/codex.json"
export RSI_REMOTE_BACKEND=envarena
export RSI_RESEARCH_AGENT_COUNT=1
uv run --env-file .env --locked --extra codex python examples/remote/run.py \
  --config examples/remote/livecodebench.yaml \
  --root .rsi/lcb-smoke --iterations 1
```

确认计划后，在同一命令末尾添加 `--launch`，执行一轮：
基线 VALIDATE → TRAIN → 提案 → 发布候选配置 → 候选 VALIDATE → 选版。

恢复已有实验时使用相同配置和数据目录：

```bash
export RSI_MODE=remote
export RSI_REMOTE_CONFIG="$PWD/examples/remote/livecodebench.yaml"
export RSI_DATA_ROOT="$PWD/.rsi/lcb-smoke"
uv run --env-file .env --locked --extra codex rsi resume RUN_ID
```

`envarena` backend 仍兼容旧的 `RSI_ARENA_CONFIG`；设置 `RSI_REMOTE_CONFIG` 时优先使用新变量。
