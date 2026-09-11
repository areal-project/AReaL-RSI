# 后台通过 Python package 接入 ARealAgentRuntime

后台 RSI Agent、ResearchAgent、Task Agent 通过 `PackageAgentRuntime` 接入；前台仍使用 DSH 原生 TUI。
RSI 管理实验循环、不可变候选、TRAIN/VALIDATE、评分和选版。Runtime 管理 Agent 执行、会话、工具和已提交事件。

每个 RSI AgentSession 启动一个本地 Python 子进程，在其中使用公开的 `build_composition` 和 Application API。
这是 Python package 嵌入，不需要启动独立 Runtime HTTP 服务。每次 `send` 创建一个 Run，后续消息显式
`resumeSession=true`；候选切换创建新 Session。会话结束关闭 composition 和子进程。

## 架构速览

```text
用户 → DSH TUI / Interaction Agent → RSI MCP → RSI 实验编排
                                               ↓
                                  PackageAgentRuntime（Python 包）
                                               ↓
                          RSI Agent / ResearchAgent / Task Agent
                                               ↓
                             按角色选择 DSH / Codex / Hermes 等 Harness
```

前台可以直接回答日常问题，也可以调用 MCP 创建、启动和查询实验。
`RSI_TUI_MODEL_API` 只控制前台模型协议；`RSI_RUNTIME_CONFIG` 控制后台 Harness。
模型地址和密钥由 `RSI_TEACHER_*`、`RSI_TARGET_*` 提供，`RSI_AGENT_MODELS` 可覆盖前台及研究角色。
更换模型或 Harness 后请重新生成实验计划，不要继续使用绑定旧配置的计划。
个人 `.env` 不提交；从 `.env.example` 复制并填写自己的连接信息。

## 安装

在 RSI 仓库运行：

```sh
uv sync --locked
# 使用 Codex 或 Anthropic SDK 时安装对应 extra：
uv sync --locked --extra codex --extra anthropic
# 使用 Claude 两个入口时：
uv sync --locked --extra claude
```

Runtime wheel 固定在 `vendor/`，版本为 `0.4.0.dev1`，来自 Runtime 提交 `223af018a1e1e3d1cd3ed8303da47553f16e929e`。
`vendor/PROVENANCE.json` 记录 wheel 与 DSH npm bundle 的摘要。它是从确定源码构建的制品，不表示已发布到 PyPI。
DSH npm bundle 仍使用原来的 `0.3.0` 制品，其来源提交单独记录在 `artifact_source_commits` 中。
消费代码只导入公开 Python API，不需要相邻 Runtime checkout 或手工修改 PYTHONPATH。

RSI 使用 `runtime_storage` Plugin，将事件与对应状态作为一条 JSONL 记录追加到
`data/state/journal.jsonl`，每条记录完成 fsync 后才交给消费者，不重写历史事件。
Runtime 的内存 StateStore 负责当前进程内的状态校验和查询，日志负责持久化和轨迹导出。
同时设置 `eventSinks: []`，关闭默认轨迹投影器及其 SQLite 索引；运行过程不使用 SQLite。
每个日志只由一个新 worker 写入，不重开已有日志，不探测、兼容或迁移旧轨迹存储。
异常退出后导出保留完整日志前缀，仅忽略末尾未写完的一行；完整行损坏则报错。
存储格式计入实验的 Runtime 配置摘要，应创建新实验，不要续跑使用其他存储配置的旧实验。

本地流式回归测试不调用付费模型：

```sh
RSI_TEST_NATIVE_HARNESSES=1 uv run --locked --extra anthropic python -m unittest discover \
  -s tests -p test_runtime_package_execution.py -k dsh -v
```

其中长流测试经过真实 DSH worker 和工具调用，逐项检查 2000 个文本片段在会话结束后仍完整、有序。

MCP 已迁移到 2.x，RSI TUI 控制桥改用 `MCPServer`，JSON-RPC 工具名和确认流程保持兼容。
DSH 额外需要 Node.js 22.19+ / 24+、pnpm 和官方 CLI。后台首次使用时自动准备共享 CLI，以及随 RSI 包分发的
Runtime 专用 npm bundle；使用随包 lockfile 冻结安装。每个 Session 拥有可写 profile 配置，依赖安装只读共享。
直接使用普通 `sdk-minimal` profile 缺少 Runtime 的 skills/session 桥，不能代替专用 profile。

## 配置与启动

保持 `.env` 中现有 `RSI_TEACHER_*`、`RSI_TARGET_*`、`RSI_AGENT_MODELS` 和 `RSI_DATA_ROOT`。
模型连接仍由 RSI 统一管理，后台 Harness 只接触本地模型代理。不要把真实密钥写入 Runtime JSON。

选择一个配置，使用**绝对路径**，因为 TUI/MCP/worker 的工作目录可能不同：

```sh
export RSI_RUNTIME_CONFIG="$PWD/examples/runtime/dsh.json"
uv run --env-file .env --locked rsi tui --setup-only
uv run --env-file .env --locked rsi tui
```

输入“请启动一个 1 轮的 Harness 自迭代实验”，确认计划后开始后台运行。
继续使用 `rsi status`、`rsi resume RUN_ID` 和 `rsi web --port 8765`。
选用可选依赖时，在 `uv run` 中同样传入对应 `--extra`：

```sh
export RSI_RUNTIME_CONFIG="$PWD/examples/runtime/codex.json"
uv run --env-file .env --locked --extra codex rsi tui
```

前台 DSH 可通过 `.env` 中的 `RSI_TUI_MODEL_API=responses` 选择原生 pi-ai Responses 适配器。
默认值 `chat-completions` 使用原 DeepSeek 适配器；两者都经本地 GatewayProxy 转发。
前台模型可单独设置，例如 `RSI_AGENT_MODELS='{"interaction-agent":{"model":"gpt-5.6-luna"}}'`。
此选项只改变前台协议，不改变后台 Harness；修改后需重启 TUI。

配置支持 `default` 和按角色覆盖的 `roles`；`research-agent` 覆盖所有研究线，各线仍有独立会话。
`examples/runtime/mixed.json` 演示 RSI Agent 使用 Anthropic、ResearchAgent 使用 Codex、Task Agent 使用 DSH。
修改 Harness 配置后创建新实验；已有实验校验配置和制品摘要，不能换引擎后直接续跑。
接入前的旧 DSH 实验请使用原 RSI 版本恢复；新实现不伪装兼容旧 DSH Session。

## Harness 配置与能力

| 配置样例 | 模型协议 / 额外环境 | 工具方式 |
|---|---|---|
| `dsh.json` | Chat Completions；自动准备 DSH CLI/profile | Application 工具桥，原生 skill 加载 |
| `anthropic.json` | Anthropic Messages；`--extra anthropic` | Application 工具 |
| `codex.json` | Responses；官方 Codex CLI、`--extra codex` | Codex 原生工具 |
| `claude-code.json` | Messages；官方 Claude CLI、`--extra claude` | 默认 portable Application 桥 |
| `claude-sdk.json` | Messages；Claude SDK/其 CLI、`--extra claude` | 默认 portable Application 桥 |
| `hermes.json` | Chat Completions；配置实际 Hermes 安装目录和 Python | Hermes 原生工具 |
| `remote.json` | 已部署的 Runtime Wire 服务；替换示例地址并配置认证 | 由远端 Adapter 能力决定 |
| `entrypoint.json` | 已安装的第三方 Adapter；替换示例 entrypoint | 由第三方 Adapter 能力决定 |
| `fake.json` | 无模型，仅开发验收 | 不代表真实工具或推理执行 |

JSON 中每个 Runtime 条目采用公开 `RuntimeBootstrapConfig` 格式。
模型/endpoint/密钥来自 RSI 的模型设置；需要不同协议时为对应角色配置兼容的模型地址。
RSI 模型代理分别转发 `/chat/completions`、`/responses`、`/messages`，不会把任意模型服务自动转换成其他协议。
Codex 可显式设置 `config.modelProtocol="anthropic"` 使用 Runtime 已有的局部 Messages 协议桥。

所有 Harness 共用配置入口，但不会获得上游没有实现的能力。Codex/Hermes 使用原生工具，不能宣称支持
portable ToolSpec 的精确授权。远端执行的隔离需要在远端部署中配置；本地沙箱不覆盖远端执行机器。

## 工具、MCP 与隔离

- portable 模式把 RSI 的 `bash@v1` 映射为已授权的 workspace 工具，其中 `workspace.run` 使用 `argv`；
  `editor@v1` 增加 write/edit。模型获得真实工具 schema，不需要用户改变日常启动命令。
- 候选 `mcp.json` 继续只允许已验证的 workspace `tools/*.py` stdio server。portable 模式先发现 schema，
  通过 RSI 的 `ToolProvider` Plugin 注册并绑定；调用和结果进入 Runtime 的规范事件。目前投影支持文本 MCP 结果。
- Codex/Hermes 没有通用 Application MCP 桥时，通过安装好的 RSI MCP CLI、使用原生 shell 工具调用同一批 server。
  模型指令包含 list/call 用法；轨迹中的规范工具事件属于原生 shell，MCP 的返回内容包含在工具输出中。
- 当前本地执行要求 macOS，每个会话使用 `sandbox-exec`。只开放会话状态目录和当前工作区写权限，不能读取相邻实验答案或其他候选目录。
  worker 从已安装的 RSI 包中提取执行所需的三个模块文件到会话目录，并禁止读取完整 RSI 包，
  避免工具从评测模块读取隐藏答案；没有复制 Runtime 内核。
  `readable_paths` 只用于可信部署明确指定的 Harness 安装/依赖目录，不要指向整个数据目录。
- Codex 使用外层 RSI 沙箱；其内层配置默认 `danger-full-access`，用于避免 macOS 拒绝嵌套 sandbox-exec。
  实际进程仍受 RSI 外层文件权限限制，该字段不意味着拥有宿主文件访问权限。
- 子进程从最小环境构建，不继承桌面 Agent 的会话、工具或宿主密钥。模型密钥保留在父进程代理。
  Remote Wire 的认证使用显式 `authTokenEnv`。不要用候选文件安装 Runtime Plugin 或选择 endpoint。

## 数据与失败

`RSI_DATA_ROOT/sessions/<trajectory>/data/` 保存 Runtime 的权威 StateStore、资产和事件出口。
`run-bindings.json` 记录同一会话的 Runtime Run IDs；RSI `trajectories/` 与 ContentStore 保存轨迹导出和业务关联。
导出从已提交事件重新读取，保留 `turns` 供 ResearchAgent 使用，包含 Runtime run/session 等原始身份。
这是可重建的投影，不是第二份执行状态机。

超时和取消会请求 Runtime 取消运行，然后关闭 composition，必要时清理子进程组。
缺依赖、无最终正文、不支持的配置或工具不会被转换成成功结果。
Runtime 数据根与工作区在失败后保留，诊断入口是对应会话的 `worker.log`。
实验恢复复用 RSI 已有检查点；进程崩溃发生在任务结果登记之前时，未完成样本可能重跑，不承诺跨进程 exactly-once。

## 验收

本次执行结果和测试范围见 [验收记录](runtime-package-validation.md)。

```sh
# 原有离线回归、类型检查、格式检查
uv run --locked --extra anthropic --extra codex pytest -o addopts='' -q
uv run --locked --extra anthropic --extra codex mypy src tests
uv run --locked --extra anthropic --extra codex ruff check src tests

# 显式允许原生 CLI 启动、DSH npm profile 安装；模型仍为本地脚本端点
RSI_TEST_NATIVE_HARNESSES=1 uv run --locked --extra anthropic --extra codex \
  pytest -o addopts='' -q tests/test_runtime_package_execution.py
```

Claude 两入口的本地执行测试按本次要求跳过。测试使用确定性本地模型，不能当作外部模型质量或生产服务可用性的证明。

Hermes 首次执行 terminal 可能自动下载 Tirith 命令检查器。建议在可信安装阶段准备并校验它，
通过 `default.config.env.TIRITH_BIN` 指向实际二进制，并让它处于已允许读取的依赖目录内。
本次独立安装位于 `~/.local/share/areal-rsi-harnesses/hermes-agent`，本机可使用
`~/.local/share/areal-rsi-harnesses/hermes-runtime.json` 作为 `RSI_RUNTIME_CONFIG`。
不要通过关闭命令检查来代替缺失依赖的安装。

补测本机 Hermes：

```sh
RSI_TEST_HERMES_ROOT="$HOME/.local/share/areal-rsi-harnesses/hermes-agent" \
  uv run --locked --extra anthropic --extra codex pytest -o addopts='' -q \
  tests/test_runtime_package_execution.py -k hermes
```
