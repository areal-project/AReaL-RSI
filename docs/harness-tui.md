# 通过 TUI 本地复跑 Harness 迭代

从仓库根目录启动。此流程新建实验，沿用正式 demo 的任务类型、初始 Harness、预算和验收门槛；模型生成有随机性，不保证复现同一候选或分数。

## 1. 准备连接和环境

当前演示机器的 `~/.areal_rsi/config.json` 已配置 Teacher、Target 为 `gpt-6.1-sol`，以及共享 DSH 安装和本机会话目录。直接使用即可；凭证不需要粘贴到 TUI 或写入实验配置。换机器时先运行 `uv run rsi config init`，按[配置说明](../README.md)填写自己的模型连接。

```bash
uv sync --locked --extra browser
export RSI_DATA_ROOT="$PWD/.rsi/local-harness-tui-20261016"
uv run --locked --extra browser rsi tui
```

`RSI_DATA_ROOT` 决定实验和历史记录的位置。第一次复跑使用上述新目录；以后另开独立实验时换目录，并选用新的题目种子。恢复同一次实验则保留原目录。

首次启动会自动安装固定版本的 DSH CLI 和 TUI profile，需要 Node.js 22.19+（或 24+）及 pnpm。只准备入口、暂不打开界面时，可运行 `uv run --locked --extra browser rsi tui --setup-only`。Harness 的账本评测不需要启动 Chromium。

网络文件系统上的 Node 依赖加载和逐事件落盘可能很慢。当前机器分别使用 `RSI_DSH_ROOT=/tmp/rsi-shared-runtime`、`RSI_SESSION_ROOT=/tmp/rsi-goal-sessions`；实验记录仍保存在仓库的 `RSI_DATA_ROOT`。`/tmp` 内容可能被清理，适合长期复跑的机器应选稳定的本机磁盘目录。如果机器配置中的 `RSI_RUNTIME_CONFIG` 显式指定了 `dshBin`，迁移安装目录时也要同步该路径；确认实验后保持 Runtime 和实现源码不变。

## 2. 在 TUI 粘贴目标

将下面整段文字发送给 TUI：

```text
我要新建并执行一次 Harness 迭代实验。请读取 examples/goals/harness.json 作为 Goal 契约模板，保持其中的预算、验收门槛和允许修改范围。

修改 assets.benchmark_seed 为 2026101601，将 assets.harness_path 转成当前仓库 examples/goals/minimal-harness 的绝对路径；research_agent_count=1，skill_paths=[]。Teacher 和 Target 使用机器配置中的 gpt-6.1-sol。目标是提高账本多步计算、逐步舍入及严格 JSON 输出的正确率，使用通用方法，不写题目专属答案。

请通过 rsi_goal_draft 建立草稿，有缺失就用 rsi_goal_answer 补充。展示完整冻结契约和模型配置摘要，等我确认后再用 rsi_goal_start 启动。启动后在预算内持续迭代，不需要逐轮询问，并返回 goal_id 和 run_id。
```

检查 TUI 展示的契约：

| 项目 | 应有设置 |
| --- | --- |
| Target / Benchmark | `harness` / `ledger-arithmetic-v1` |
| 初始基线 | `examples/goals/minimal-harness` 的快照 |
| 数据 | 新种子 `2026101601`；TRAIN 30、VALIDATE 30、FINAL 60 |
| 研究线 / 评测并发 | 1 / 8 |
| 预算 | 最多 5 轮、7200 秒、最多 1 次重试 |
| 最终验收 | 正确率 ≥90%；比冻结基线提高 ≥10 个百分点；结构化输出检查通过 |
| Verifier / FINAL 协议 | `ledger-arithmetic-v1-verifier` / `independent-holdout-v1` |
| 可修改文件 | `prompt.md`、`skills/*.md`、`tools/*.py`、`mcp.json` |
| 固定项 | 模型、Benchmark 和 Verifier；不加载 Research Skill |

确认无误后，在 TUI 回复：

```text
确认上述完整契约，启动这个 Goal，并在约定预算和范围内持续完成迭代与独立最终验收。
```

确认前只有草稿；确认后才会执行基线评测和研究。每轮研究使用 TRAIN 与 VALIDATE 聚合结果；达到开发条件后冻结候选，再对基线和候选运行独立 FINAL。当前代码同时检查开发质量和相对增益，可能使用多轮或耗尽预算。

## 3. 查看、暂停和恢复

TUI 中可以说“查询 Goal GOAL_ID 的状态和当前阶段”“暂停 Goal GOAL_ID”“恢复 Goal GOAL_ID”或“取消 Goal GOAL_ID”。将占位符替换为启动返回的真实 ID。

另开终端，在同一仓库根目录使用相同的数据根目录查看只读面板：

```bash
export RSI_DATA_ROOT="$PWD/.rsi/local-harness-tui-20261016"
uv run --locked --extra browser rsi web --port 8765
```

打开 `http://127.0.0.1:8765`，查看轮次、版本差异、轨迹及独立最终分数。如果程序运行在远端服务器，需要将远端 8765 端口转发到本机后访问。

关闭 TUI 不会自动停止后台实验。重新运行相同的 `rsi tui` 并提供 Goal ID 即可继续查询；`rsi tui --resume SESSION_ID` 恢复的是对话。暂停后的实验需要显式恢复。命令行也能操作同一份状态：

```bash
uv run --locked --extra browser rsi goal status GOAL_ID
uv run --locked --extra browser rsi goal pause GOAL_ID
uv run --locked --extra browser rsi goal resume GOAL_ID
uv run --locked --extra browser rsi goal export GOAL_ID --destination ./delivery-harness
```

`achieved` 表示通过独立最终验收；`budget_exhausted` 表示预算用尽；`failed` 保留未通过原因。底层 Run 的 `succeeded` 仅表示执行流程完成。

## 4. 与历史实验的关系

上次正式实验使用种子 `20261025`，FINAL 基线 54/60、候选 58/60，提升 6.67 个百分点，没有达到 +10 个百分点门槛。完整报告见[实测结果](../examples/goals/RESULTS.md)。该次实验使用冻结的旧实现；当前实现已修复开发阶段的相对增益停止条件，因此新实验可能经历不同的轮数。

新目录与新种子用于重新进行实验。历史 FINAL 已暴露，不应继续用于效果选版；最终验收失败后当前搜索关闭，不能靠恢复该失败 Goal 继续调整候选。后续研究需要新实验及未暴露的最终任务。若只想查看上次历史，在另一个终端运行：

```bash
RSI_DATA_ROOT=.rsi/demos/harness-final uv run --locked --extra browser rsi web --port 8766
```

本机保留的运行历史不随 Git 提交分发；仓库包含报告、交付候选和冻结源码归档。
