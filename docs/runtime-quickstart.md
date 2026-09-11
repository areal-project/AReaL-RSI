# AgentRuntime 接入简要说明

## 接入形式

后台 RSI Agent、ResearchAgent、Task Agent 通过标准 Python package 调用 ARealAgentRuntime。
每个 AgentSession 在独立沙箱子进程中创建 Runtime composition，不需要单独部署 HTTP 服务。
前台仍是 DSH TUI / Interaction Agent，通过 RSI MCP 管理后台实验。

## 启动

```sh
uv sync --locked
cp .env.example .env
# 填写 RSI_TEACHER_* 和 RSI_TARGET_* 的模型、地址与密钥。
uv run --env-file .env --locked rsi tui
```

macOS 本地执行需要 Node.js 22.19+（或 24+）和 pnpm。默认后台选择 DSH，首次启动自动安装固定版本 CLI/profile。

在对话里问“怎么启动 RSI 实验？给我一个 demo”即可获得最小示例。
local / remote 都使用实验 YAML，分别见 `examples/local/arithmetic.yaml`、
`examples/remote/livecodebench.yaml` 和 `examples/tb21/experiment.yaml`。
在同一会话说“用 local 算术 demo 生成一轮计划”或“用 remote LCB demo 生成一轮计划”，
也可指定 YAML 路径，不需要重启。每份计划有独立目录和配置快照，确认时按 plan_id 执行。
对 TB2.1 也可以直接说“在Arena上使用gpt-5.6-sol terminus@1.0.10优化TB2.1的表现”；
TUI 会选择 `tb21` preset 并生成待确认计划。
三份 demo 都明确指定 `research_harness: dsh`，通过 ARealAgentRuntime 的 DSH 执行研究角色。
该字段优先于旧 Runtime JSON 的研究角色配置；Task Runtime 仍独立配置。

## 切换模型和 Harness

- `RSI_TEACHER_*`：前台、RSI Agent、ResearchAgent 的默认模型连接。
- `RSI_TARGET_*`：Task Agent 的模型连接。
- `RSI_AGENT_MODELS`：前台或研究角色的模型覆盖。
- `RSI_TUI_MODEL_API`：前台使用 `chat-completions`（默认）或 `responses`。
- `RSI_TURN_TIMEOUT`：后台单次 Agent turn 的超时，默认 1200 秒（20 分钟）。
- `RSI_RUNTIME_CONFIG`：后台配置文件的绝对路径；样例见 `examples/runtime/`。
  支持 DSH、Codex、Claude Code/SDK、Anthropic、Hermes、Remote、Entrypoint、Fake；能力以各适配器为准。

Codex、Anthropic、Claude 分别需要对应 Python extra；原生 CLI 和远端服务需要按样例准备。
修改模型连接环境后需重新加载环境；切换实验模式或 YAML 只需生成新计划。
旧 DSH 版本实验使用原版本恢复。

## 数据与验证

Runtime 保存权威事件和资产；RSI 轨迹是兼容导出。后台保留工作区隔离、RSI 工具和候选 MCP。
个人 `.env`、密钥和实验数据不提交 Git。Runtime wheel 与 DSH npm bundle 固定在仓库，来源及摘要随制品记录。

完整配置、能力边界见 [接入指南](runtime-package.md)，实际测试与跳过项见 [验收记录](runtime-package-validation.md)。
