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

ResearchApp 自主安排基线 TRAIN 评测、研究员改写、候选 TRAIN 评测与最终 VALIDATE。
`verify_candidate(search)` 返回 TRAIN 反馈和完整目标模型轨迹；`final` 只反馈最终评分。
最终确认后调用 `finish`。没有固定轮次调度器，也不调用另一套应用。
