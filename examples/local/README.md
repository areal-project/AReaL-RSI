# 最小算术 local 测试

与 remote 相同，实验 YAML 选择 Benchmark、初始 Harness、被测模型和研究 Harness。
`arithmetic.yaml` 使用内置的 3 道 TRAIN、3 道 VALIDATE 算术题及其 ArithmeticVerifier。

```bash
uv run --env-file .env --locked rsi tui
```

在 TUI 中问“怎么启动 RSI 实验？给我一个 demo”，或输入
“用 local 算术 demo 生成一个 1 轮的计划”，核对后回复“确认”。
也可在对话中指定 `examples/local/arithmetic.yaml`。其 `mode: local` 决定后端，
同一会话可再选择 remote LCB demo，不需要重启。

`model: target` 复用 `.env` 中的 `RSI_TARGET_MODEL / BASE_URL / API_KEY`。
也可把 `model` 改成模型名，继续使用 `RSI_TARGET_BASE_URL / API_KEY` 的连接。
`harness: default` 使用包内初始能力目录，也可填写相对于 YAML 的目录路径。
`research_harness: dsh` 指定 ARealAgentRuntime 的 DSH；Task Runtime 单独由 Runtime 配置决定。
计划数据保存在 `.rsi/experiments/` 下独立的目录中，配置在生成计划时保存快照。
`RSI_LOCAL_CONFIG` 仍供旧的非交互调用使用；TUI 通过 `rsi_plan` 选择配置。

旧的 `RSI_BENCHMARK`、`RSI_GSM8K_DATA`、`RSI_TRAIN_LIMIT`、`RSI_VALIDATE_LIMIT`
仍兼容。显式提供 local YAML 时，以 YAML 为准。GSM8K YAML 可使用
`benchmark: gsm8k`、`dataset`、`train_limit`、`validate_limit`，Verifier 由 Benchmark 绑定。
