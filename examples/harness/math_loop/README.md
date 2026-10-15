# 单题数学 harness 迭代

用于重构后的本地端到端验收：一位 Leader 在真实 `ResearchSystem` 中执行
**评测 → 总结经验 → 修改 harness → 重新评测**。无需 GPU、数据下载或 EnvArena。

在仓库根目录配置 `.env` 的研究模型连接后运行：

```bash
RSI_BACKEND=codex uv run --env-file .env --locked rsi prepare math-loop \
  --directory .rsi/math-loop/setup --time-limit 900
uv run --env-file .env --locked rsi research \
  --config .rsi/math-loop/setup/research.json --data-root .rsi/math-loop
```

也可将第二条换成 `rsi tui --config ... --data-root ...`，在 TUI 中输入 `/start`。
每次新实验用新的 setup 目录，避免覆盖已冻结的配置。上述命令选择 Codex 后端，
仍调用 `.env` 配置的模型 API，不需要本地 Codex 登录；端点需要支持 Responses 和工具调用。
若要验证 standard 后端，将首条命令的 `RSI_BACKEND=codex` 改为 `RSI_BACKEND=standard`。
准备时会固定后端；运行命令无需再次设置。环境变量优先于 `--backend` 的默认设置。

正常路径是四次目标模型解题：基线一次、两版改进各一次、选中版本最终复测一次。
每次解题最多 120 秒，示例总预算为上述命令设置的 900 秒；耗时取决于模型和网关，
不保证总能在预算内完成。首次运行还可能需要安装 Runtime 依赖。
预算耗尽、API 故障或验证失败会保留记录，不算通过。

## 模型配置

此示例在 `RSI_TARGET_MODEL`、`RSI_TARGET_BASE_URL`、`RSI_TARGET_API_KEY` **全部为空**时，
默认使用研究模型的连接。Leader 与解题模型仍是独立角色、独立会话，目标连接在准备时固定。
如需不同的目标模型，填写：

```dotenv
RSI_TARGET_MODEL=your-target-model
RSI_TARGET_BASE_URL=https://your-endpoint.example/v1
RSI_TARGET_API_KEY=your-key
```

不完整的目标配置会报错，不会混用两套凭据。已有 arithmetic/LiveCodeBench 示例的配置行为不变。
研究角色使用本地 Codex 登录时，请显式配置上述目标 API 连接。
示例不把密钥写入生成的 research.json 或 manifest.json。

## 题目与迭代要求

固定题目：由 18 个上升步和 18 个下降步组成的格路径，起终点高度为 0，全程高度在
0 到 6 之间，恰有 5 个 `UD` 峰，且每一步出发前的高度之和能被 7 整除。求路径总数。
这是需要处理多重约束和边界条件的组合计数题；目标模型可以在沙箱内运行 Python。
题目公开，精确答案由宿主动态规划计算，评分器和答案不放入模型工作区。

| 阶段 | 动作 | 必须留下的记录 |
| --- | --- | --- |
| v0 | 保存原始 harness，diagnostic 评测 | 候选快照、真实反馈和解题轨迹 |
| v1 | 阅读 v0 反馈，save_evidence，总结后修改并保存，search 评测 | 引用 v0 verification 的经验、不同内容的子版本及评测 |
| v2 | 阅读 v1 反馈，再总结、修改、保存、search 评测 | 引用 v1 verification 的经验、第三份内容及评测 |
| final | 选择 v1 或 v2，在新会话复测，再验收 run | 同一候选的最终评测、loop.json、验收记录 |

只有 `prompt.md` 和 `skills/*.md` 可修改。经验通过 `save_evidence` 保存，引用对应
`run://.../verifications/....json`，不要把题目答案、实验笔记或临时程序塞进 harness。
通用推理策略可以改进状态建模、边界检查、独立核验与输出格式。

最终评测前，宿主检查至少三个不同内容的父子版本、每次评测完成后且下次版本保存前
产生的经验记录及其引用。仅更改候选描述、重复提交同一内容、只完成一轮改进、没有经验引用，
都不能通过；该检查验证记录链路，经验的数学质量仍需查看实际内容。
中途无修改的重复快照不计入版本数，也不会截断后续真实改进的祖先链。
起点已经答对也必须完成两轮改进。选中较早的 v1 可以通过，不强制选择最新版。

**全部 accuracy=0 也可以完成循环验收**；基础设施错误不能冒充 0 分。
final 是同一题的新会话复测，仍标记为 TRAIN，不宣称有独立 VALIDATE 或泛化提升。
正常路径只做一次 final；由于没有隐藏题池，错误提交 final 后允许修正并重新选择版本。

## 查看结果与回归

命令输出包含 run_id。运行目录位于 `.rsi/math-loop/runs/<run_id>/`：

- `candidates/`：不可变版本、父版本与内容摘要。
- `verifications/`：各版本和 final 的可信结果，`score` 是答题正确率。
- `evidence/`：每轮观察、假设、经验和引用。
- `execution-outputs/<final_job_id>/public/loop.json`：三版候选、对应评测、两轮经验、
  选中版本和最终分数，适合快速检查整个循环。
- `acceptance/run.json`：Leader 完成 run 的验收依据；单有候选或分数不代表 run 完成。

`execution-outputs/<job_id>/public/bounded_paths.json` 提供 diagnostic/search 的模型输出和轨迹。
失败后可以用原目录在 TUI 中恢复；模型调用失败时用 `/retry`：

```bash
uv run --env-file .env --locked rsi tui --data-root .rsi/math-loop --resume <run_id>
```

不调用外部模型的快速回归：

```bash
uv run --locked pytest tests/test_math_loop.py
```

测试使用可控的 Leader 和目标模型输出，实际经过 `ResearchSystem`、后台评测子进程、
候选快照、经验引用、最终复测、验收、重开和导出。它验证三版均答错仍可完成循环、
不足三版及重复内容被拒绝，并独立核对计数器；不代表真实模型已经完成端到端运行。
