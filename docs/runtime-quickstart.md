# Runtime 启动

现在只有新研究模式。研究角色使用持久化 Responses 会话；TUI、Web 与 CLI 共用 ResearchEngine。旧计划启动和固定工作流已删除；Task Runtime JSON 与存储插件保留，详见 [Runtime 接入](runtime-package.md)；DSH TUI plugin 保留，MCP 直接连接新研究 API。

完整命令见[项目启动指南](../README.md)。数学与 LCB：

```bash
uv run --env-file .env --locked rsi prepare --case gsm8k --dataset .rsi/datasets/gsm8k --output .rsi/configs/gsm8k/research.json
uv run --env-file .env --locked rsi tui --config .rsi/configs/gsm8k/research.json
# 在 DSH 对话里说“开始这项研究”
```

LCB 将 `--case` 换为 `lcb` 并使用新的输出目录。无需 `local-app register` 或 `RSI_MODE`。可在 prepare 前用 `RSI_RUNTIME_CONFIG` 指定数学 Task Runtime。

被测 Task Agent 与研究模型分离。数学/KernelBench Harness 的本地 Task Agent 使用固定 DSH SDK 和 macOS sandbox-exec，首次准备 CLI 需要 pnpm。LCB 的 Task Agent 与评分在 EnvArena 执行。
