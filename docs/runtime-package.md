# AReaL Agent Runtime 接入

ResearchApp 是研究应用入口。它通过 `ResearchRuntime` 调用原有的
`PackageAgentRuntime`，使用 AReaL Agent Runtime 的公开 Python composition/Application
API。每个会话有独立的 Python worker，不需要单独部署 Runtime HTTP 服务。

```text
Web / Research CLI → ResearchApp → ResearchRuntime → PackageAgentRuntime
                                                     └─ DSH / Codex / Hermes / 其他适配器
```

研究模型使用 `RSI_MODEL / RSI_BASE_URL / RSI_API_KEY / RSI_REASONING_EFFORT`。
后端由 ResearchConfig 的 `backend` 或启动前工作台选择决定，研究模型连接在启动时固定，Runtime 配置可按角色选择适配器。Task benchmark 的被测模型使用独立 `RSI_TARGET_*` 或远端固定模型引用。

## 制品与执行

Runtime wheel 固定在 `vendor/`，版本为 `0.4.0.dev1+rsi1`。来源和摘要见
`vendor/PROVENANCE.json` 及 `resources/runtime/provenance.json`。
DSH 需要 Node.js 和 pnpm；首次启动安装随包冻结的 CLI/profile 依赖。

每次 `send` 创建一个原生 Run，后续消息继续使用已绑定的 Session。
Runtime 管理推理、工具、压缩和供应商重试，应用管理实验消息、权限、作业和验证。
ResearchApp 提供 standard/codex 和 runtime（受信配置文件）入口。

## 工具与隔离

RSI 研究工具通过会话令牌访问父进程，由父进程检查角色授权和参数。
DSH 使用 ToolProvider；Codex 通过原生命令工具调用会话内 `rsi-tools`。
Task harness 的候选 MCP 配置仍通过独立 Runtime 工具桥发现和执行。

默认模式使用 macOS `sandbox-exec`，只开放当前工作区和会话状态目录写权限。
worker 只复制执行所需的模块，禁止读取完整 RSI 包中的评测答案。
可信 Linux 可显式设置 `RSI_RUNTIME_ISOLATION=linux-unrestricted`，此时进程拥有
启动用户权限，不提供文件、进程或网络隔离。详见[后端说明](agent-backends.md)。

子进程采用最小环境，不继承桌面 Agent 会话、宿主工具或模型密钥。
模型请求由父进程代理，候选不能指定 endpoint 或安装 Runtime Plugin。

macOS 文件隔离下，Codex 使用进程专属的 HTTPS CONNECT 通道和明确的 CA 证书
（未配置 `SSL_CERT_FILE` 时使用 certifi）。通道只转发加密字节，不改写模型请求、
API/key 或重试策略；默认只允许公网 443，另允许部署配置中明确指定的模型端点。
解析后连接固定 IP，拒绝其他本地/私网目标；各进程的沙箱只开放自己的通道端口。
这避免依赖沙箱内无法正常使用的 macOS 网络/证书服务，也不会开放系统代理或其他 run。
实现位于 `modules/runtime_network.py`，独立于 Runtime wheel 的计时事件补丁。

DSH / Claude 的 MCP 工具桥由上游动态选择端口，因此这些后端允许访问 localhost，
不再提供精确的本地端口隔离；文件沙箱仍保留。此 PR 不新增 Runtime 补丁，沿用 main 的
`+rsi1`（已有 Codex 可用性事件补丁）。RSI 工具桥的会话令牌和角色检查仍生效。

## 日志与失败

`runtime_storage` Plugin 将事件与状态追加到 `data/state/journal.jsonl`，提交后才向
消费者发布。`session-binding.json` 固定原生会话身份，`run-bindings.json` 记录原生 Run。
轨迹导出是日志投影，不是第二套执行状态机。

当前格式的会话重启时重放规范日志；取消会请求原生运行停止并清理子进程组。
缺依赖、无最终正文、工具或配置错误不会被当成成功结果。旧格式不自动迁移。

## 验证

```bash
uv run --env-file .env --locked rsi preflight --backend standard
uv run --env-file .env --locked rsi preflight --backend codex
RSI_TEST_NATIVE_HARNESSES=1 uv run --locked pytest \
  tests/test_preflight.py tests/test_research_runtime_package.py tests/test_tool_call_identity.py
```

前两条命令调用配置的真实模型。pytest 使用本地脚本模型，检查原生工具调用、会话
续接、重试和重复交付，不作为模型效果评测。端到端验收还需要真实实验与可信评分。
