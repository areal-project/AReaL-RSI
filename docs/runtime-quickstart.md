# ResearchApp 快速启动

```bash
uv sync --locked
# 填写 .env 中的 RSI_MODEL / RSI_BASE_URL / RSI_API_KEY
uv run --env-file .env --locked rsi workbench
```

默认入口 `rsi`、`rsi web` 和 `rsi workbench` 打开同一工作台。
完整配置可以直接运行：

```bash
uv run --env-file .env --locked rsi research --config /absolute/path/research.json
uv run --env-file .env --locked rsi research --resume RESEARCH_ID
```

模型、后端和协作模式均在研究开始前确定。standard 使用 DSH，codex 使用 Codex CLI，
两者均通过 `PackageAgentRuntime` 执行。数学、LCB 和 KernelBench 使用同一个
ResearchApp，仅实验命令和验证器不同。

- [数学示例](../examples/local/README.md)
- [LCB 示例](../examples/remote/README.md)
- [KernelBench 单题](../examples/kernelbench_artifacts/README.md)
- [后端与隔离](agent-backends.md)

固定轮次 CLI、TUI/MCP 实验控制器已移除；不存在独立的实验 worker 入口。
