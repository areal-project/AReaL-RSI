# Python package 接入验收记录

日期：2026-09-08。环境：macOS / Python 3.13.11。
RSI 基于 `c02c886`，工作分支 `codex/runtime-package-integration`；Runtime 制品来自 `971ad809f4dee80e605597b4500cc4494eab27b5`。

## 结果

- 全量 RSI 测试：**148 passed、2 skipped、52 subtests passed**，耗时 69.64 秒。
- 独立虚拟环境安装 RSI wheel 和 Runtime wheel，在 checkout 之外以 `python -I` 执行：
  **9 passed、2 skipped、4 deselected**，耗时 25.91 秒。4 个未选择项为原生 DSH/Codex/Hermes 测试，已在全量回归执行。
- Ruff：通过。Mypy：73 个源文件通过。`git diff --check`：通过。

全量运行启用了 `RSI_TEST_NATIVE_HARNESSES=1`，以及实际安装目录对应的
`RSI_TEST_DSH_ROOT`、`RSI_TEST_HERMES_ROOT`。

## 验证范围

| 入口 | 验证 |
|---|---|
| DSH | 实际 CLI、随包 Runtime npm profile、工具调用；额外验证共享官方 CLI 安装路径 |
| Codex CLI | 实际 Codex 0.153.4 app-server、Runtime Messages 协议桥、原生工具调用 |
| Anthropic | 实际 SDK、workspace 工具、候选 MCP 工具、完整 RSI 实验循环 |
| Hermes | 实际 Hermes 0.17.0（`2f5950a83a66d2b91918bb78e6d1b60f3f48b938`）、原生 terminal 工具 |
| Remote | 本地独立 Wire 服务、显式认证变量、远端 Fake Harness，未验证生产远端执行环境 |
| Entrypoint | 加载已安装包的公开 Fake Plugin，未验证任意第三方 Plugin |
| Fake | 会话续接、多个 Run、轨迹导出和清理 |
| Claude Code / Claude SDK | 配置接入；按用户要求跳过本地执行，SDK 入口同样依赖 Claude Code |

所有执行使用本地确定性脚本模型，没有调用付费模型服务，不表示外部 LLM 兼容性、推理质量或真实自迭代收益已经验收。
还验证了取消提交终态事件、子进程清理、禁止读取相邻实验文件及 RSI 包中的评测答案。
本地文件隔离目前要求 macOS `sandbox-exec`；远端执行环境需要远端自行配置隔离。

Hermes 的 Tirith v0.4.1 按官方发布的 SHA256 校验后安装，未关闭命令检查：
`87e17384a9a905c64acc4d5d9396def638e80b1e703ef1def75f549cf3d212f9`（aarch64 macOS 发布归档）。

使用说明及各 Harness 配置见 [接入文档](runtime-package.md)。

## 提交前补充验证

- 前台增加 `RSI_TUI_MODEL_API=responses`，使用 DSH 自带 pi-ai 适配器；相关入口及代理回归 13 项通过。
- Hermes 的独立 uv Python 链接补充只读元数据权限；修复后 Hermes 执行与评测文件隔离检查 2 项通过。
- 使用本机配置对 DeepSeek `deepseek-v4-flash-vision-exp` 做过一次真实 Chat Completions 最小请求，
  HTTP 200、返回 `OK` 并收到 `[DONE]`，约 6 秒。此结果仅证明当时模型连接可用，不代表完整真实实验已验收。
- 模型地址、密钥、用户 `.env` 和实验状态均不包含在提交中。
