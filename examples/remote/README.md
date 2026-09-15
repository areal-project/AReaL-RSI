# LiveCodeBench 研究

通过 EnvArena 远端执行原有两题：TRAIN 为 LCB 2727，VALIDATE 为 LCB 2777，每题沿用原 LCB 测试和评分。研究员只修改候选 `prompt.md`，适配器发布新的 system prompt Harness，保留远端 Runtime、模型、环境和 Reward。

## 准备与启动

1. `arena doctor` 确认远端认证正常。使用 `ARENA_OPENAPI_TOKEN` 或 `~/.envarena/config.toml`。
2. 在 `.env` 配置研究角色的 Responses 模型连接。被测模型由 YAML 中固定的远端 Model 版本决定，不使用本机 RSI_TARGET 密钥。
3. 生成配置并启动：

```bash
uv run --env-file .env --locked rsi prepare --case lcb \
  --remote-config examples/remote/livecodebench.yaml \
  --output .rsi/configs/lcb/research.json
uv run --env-file .env --locked rsi tui --config .rsi/configs/lcb/research.json
```

在 DSH TUI 中说“开始这项研究”。直接运行可使用：

```bash
uv run --env-file .env --locked rsi research --config .rsi/configs/lcb/research.json
uv run --env-file .env --locked rsi research --resume <run_id>
```

当前配置固定 `rsi-lcb-claude-code-smoke@1.0.5`、其绑定 Reward、`rsi-lcb-claude-code@1.0.5` Harness 及 YAML 指定 Model。准备阶段只读检查远端实体；正式实验会创建候选 Harness、子集 Benchmark 和 Job。

训练作业提供任务输出与反馈；search/final 仅向研究员提供聚合结果。final 是同一留出题池上的独立执行复测，不是新的测试集。远端回执位于研究对应执行输出的 `private/remote/executions/`，可据其中 job_id 到平台核查。

旧 RemoteApp、固定演化循环、`rsi resume` 和 `examples/remote/run.py` 已移除。远端 Task 执行和可信评分能力由新研究模式调用。
