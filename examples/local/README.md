# 数学：通过 ResearchApp 研究 Task harness

使用内置原创 `arithmetic-v1`，包含 3 道 TRAIN、3 道 VALIDATE；不冒充官方 GSM8K。
研究模型通过 ResearchApp 改写通用 prompt/skill，固定 Task 模型执行每道题，宿主
ArithmeticVerifier 检查最终答案。正确答案不会复制到研究员或 Task 工作区。

配置 `.env` 中的研究模型 `RSI_*` 和被测模型 `RSI_TARGET_*`，然后执行：

```bash
uv run --env-file .env --locked python -m areal_rsi.adapters.benchmark_research prepare \
  --benchmark math --directory .rsi/math-research/setup
uv run --env-file .env --locked rsi research \
  --config .rsi/math-research/setup/research.json --data-root .rsi/math-research
```

也可将生成的 ResearchConfig 导入工作台。准备命令只固定配置，不启动研究。

ResearchApp 使用默认 v0.2.2 pass@k 协调配置，研究员固定为 1 名。
研究员先保存自己的起点候选，用 `verify_candidate(diagnostic)` 做 TRAIN 基线诊断；
再改写通用 prompt/skill、保存新候选并用 `search` 做正式 TRAIN 评测。
诊断成绩不参与选优，完整 TRAIN 轨迹随公开反馈提供。
探索预算结束后，系统冻结最佳正式候选并独立执行 VALIDATE；研究员不调用 `final` 或 `finish`。
默认探索时限 1200 秒，可在准备时用 `--time-limit` 调整，最终复测另计时。
