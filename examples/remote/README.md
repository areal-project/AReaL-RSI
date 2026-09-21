# LCB：通过 ResearchApp 研究远端 Task harness

使用 `livecodebench.yaml` 固定的 EnvArena Benchmark、Harness、Model 和 Reward。
2727 为 TRAIN，2777 为 VALIDATE；评分沿用原 LCB 测试。研究员只能修改 `prompt.md`，
评测命令发布允许的 system prompt 变更，复用远端 Task Runtime 和评分器。

在 `.env` 配置研究模型 `RSI_*`。Arena 认证使用 `ARENA_OPENAPI_TOKEN` 或
`~/.envarena/config.toml`，不会写入研究配置。

```bash
uv run --env-file .env --locked rsi examples prepare livecodebench \
  --arena-config examples/remote/livecodebench.yaml \
  --directory .rsi/lcb-research/setup
uv run --env-file .env --locked rsi research \
  --config .rsi/lcb-research/setup/research.json --data-root .rsi/lcb-research
```

`rsi examples` 可查看当前完整可运行的示例。两条命令分别准备配置与通过 ResearchApp
启动研究；Web 同样可以导入生成的配置。
使用默认 v0.2.2 pass@k 协调配置，固定 1 名研究员；研究员执行 TRAIN 基线诊断
（diagnostic）及新候选正式评测（search），系统在探索到时后冻结最佳候选并独立执行
VALIDATE（final）。默认探索时限 1200 秒，可用 `--time-limit` 调整，最终复测另计。
任务模型和评分器始终固定。实际 Job ID、原始轨迹和反馈归档在验证作业下，研究员只获得明确公开的 TRAIN
产物。远端 API 错误按基础设施失败处理，不伪造分数。
