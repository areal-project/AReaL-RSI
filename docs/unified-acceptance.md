# 数学与 LCB 统一架构验收

日期：2026-09-15。迁移前为 `6253e00`；迁移分支基于 PR #28 的 `a49717a`，移除旧编排并接入数学与 LCB。

## 判定标准

真实模型执行；训练和留出题目固定；至少一名研究员提交报告；至少一个真实修改的候选完成评测；新模式对选定候选完成 final 复测。模拟分数、单元测试通过和单独的成功状态均不能替代这些证据。

### 迁移前

| Case | Run | 结果 |
| --- | --- | --- |
| GSM8K | `run_61b7adde2a374f37` | succeeded；训练 2/2，候选验证 2/2；完成 1 轮 prompt 修改 |
| LCB | `run_274fc6acb1604f4c` | succeeded；训练 1/1，候选验证 1/1；完成 1 轮 system prompt 修改 |

首次执行遇到已失效的模型凭据（HTTP 401）；更新配置后重新创建实验。失效尝试不计入通过结果。

### 新模式首轮

| Case | Run | 结果 |
| --- | --- | --- |
| GSM8K | `research_be75811f1acb4b1d` | succeeded；baseline/search/final 均 2/2；1 份研究员报告、1 个修改候选 |
| LCB | `research_8a60237a34674161` | succeeded；baseline/search/final 均 1/1；1 份研究员报告、1 个修改候选 |

首轮暴露了训练公开目录设置过宽的问题：数学 Runtime 私有目录中的符号链接妨碍公开反馈读取。修复为 `output_subdir=public`，增加带私有符号链接的输出访问回归测试。下节记录最终配置的复验。

### 最终配置复验

| Case / 入口 | Run | 最终结果 |
| --- | --- | --- |
| GSM8K / CLI | `research_72d188f4981d4f3d` | succeeded；最终 2/2；修改候选 `candidate_7295513fd659464ff73bbdb8` |
| LCB / TUI | `research_6ccc9b7e9681467d` | succeeded；最终 1/1；修改候选 `candidate_04895fcd84f877b32f650840` |

两次均完成 1 份研究员报告、baseline/search/final 三次验证与真实训练作业。GSM8K 20 次、LCB 25 次研究模型调用。脚本逐项比对迁移前后的训练和验证 Task ID，完全一致。

LCB 的 TUI 创建并 `/start` 后执行 `/quit`；HTTP 状态确认 worker 仍然存活并继续调用模型，最终自动完成。四个远端 Job 经实时 GET 再次核对均为 DONE、score=1；最终复测为 `rsi-lcb-remote-83391dbe4fa2-2609150718`。

结构化证据见 [验收结果 JSON](unified-acceptance.json)。原始会话与 Task 输出保存在本机 `.rsi/acceptance/final-runs/`，不提交凭据或私有运行档案。

## 题目与结果含义

- GSM8K：官方 train 固定划分，TRAIN 为 `gsm8k_train_1336`、`gsm8k_train_3992`；VALIDATE 为 `gsm8k_train_1444`、`gsm8k_train_2869`。
- LCB：TRAIN 为 `rsi-lcb-claude-code-lcb-2727`；VALIDATE 为 `rsi-lcb-claude-code-lcb-2777`。
- search/final 复用相同留出题池，final 是独立执行，不是新的独立测试集。
- 小题数验收证明链路可用，不证明泛化提升。基线已满分，本次不声称准确率改善。

## 可重复检查

```bash
uv run --locked python scripts/audit_research.py --root <RSI_DATA_ROOT> --run <run_id>
```

脚本检查真实状态、模型调用、研究员报告、修改候选、训练执行、候选 search 和选定候选 final、Task 轨迹及远端 collected 回执。不读取模型密钥，不修改结果。

自动化检查：`make check` 通过，141 tests / 71 subtests，Ruff 与 Mypy 通过；前端 `npm test --prefix tests/frontend` 为 5/5 通过。

## TUI 验证

已在真实 PTY 中执行 `/new`、`/list`、`/quit`，确认终端退出后工作台服务仍在。新的 TUI 通过 HTTP 使用与 Web 相同的 ResearchApp，不运行另一个研究 Agent。LCB 最终复验还验证了配置创建、`/start` 和退出后后台继续执行。
