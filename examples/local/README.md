# 最小算术研究

内置 3 道 TRAIN、3 道 VALIDATE 原创算术题；不是 GSM8K。用真实 Task Agent 和 ArithmeticVerifier 检查研究链路。

```bash
uv run --env-file .env --locked rsi prepare --case arithmetic \
  --output .rsi/configs/arithmetic/research.json
uv run --env-file .env --locked rsi tui --config .rsi/configs/arithmetic/research.json
```

输入 `/start` 开始。也可用 `rsi research --config .rsi/configs/arithmetic/research.json` 直接运行。配置研究模型的 `RSI_*` 和被测模型的 `RSI_TARGET_*`，本地 Task Agent 需要 macOS sandbox-exec、Node 与 pnpm。

不再读取 local YAML、RSI_BENCHMARK 或旧固定工作流配置。所有入口共用 ResearchEngine。
