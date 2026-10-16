# Goal 持续生产模式

Goal 与底层 Run 分开记录：Run 的 `succeeded` 表示编排完成，Goal 只有通过独立最终验收才为 `achieved`。旧运行显示“历史运行已完成，目标达成未验证”。

通过原生 TUI 复跑账本 Harness 实验，见[操作指南](harness-tui.md)，包含启动命令、可粘贴的目标、冻结参数和状态查看方式。

## 入口与配置

```bash
uv sync --locked --extra browser
uv run --extra browser playwright install chromium
uv run rsi exec "提高数学任务准确率" --target harness --json
uv run rsi exec --spec examples/goals/task.json --yes --json
uv run rsi goal status GOAL_ID
uv run rsi goal answer GOAL_ID --answers answers.json --yes
uv run rsi goal pause GOAL_ID
uv run rsi goal resume GOAL_ID
uv run rsi goal cancel GOAL_ID
uv run rsi goal export GOAL_ID --destination ./delivery
```

自然语言请求缺字段时，非交互入口返回草稿 ID 与问题，退出码 3。完整契约必须包含 description、target、acceptance、budget、allowed_paths、invariants；Artifact 还需要 artifact_path。交互终端可补充 JSON 字段并确认。`--yes` 仅确认完整契约。`--detach` 脱离监控，后台 worker 继续；关闭 TUI/MCP/附着 CLI 不会自动取消。TUI 的 rsi_goal_* 工具调用同一个 GoalService。

JSON stdout 是带 schema_version、goal_id、revision、run_id、type 的 NDJSON；诊断走 stderr。退出码：0 达成，2 输入/配置错误，3 待补充或确认，4 预算耗尽，5 缺依赖阻塞，6 失败，130 中断/暂停/取消。状态记录是恢复依据，CLI 断连不是失败证据。

`RSI_SESSION_ROOT` 可将 Runtime 的逐事件状态存储放在本机磁盘；各数据根目录使用独立摘要子目录，完成轨迹仍进入实验内容仓库。网络文件系统上的逐 token fsync 可能显著拖慢会话。

机器配置位于 `~/.areal_rsi/config.json`，环境变量（含空值）优先。Teacher、Target 可配置同一模型连接；认证只存机器配置，不写 Goal。Runtime 配置及显式指定的本地可执行文件内容纳入身份摘要；运行中修改或删除文件会被拒绝，恢复前需还原冻结内容或创建新 revision。`RSI_DSH_ROOT` 可指定共享安装目录，`RSI_RUNTIME_CONFIG` 可选择 Runtime。Linux 需要可用的 user/mount/PID namespaces；macOS 使用 sandbox-exec。缺隔离能力明确失败，不使用无隔离降级。

TUI 同样使用 `RSI_DSH_ROOT` 保存共享 DSH 安装、profile 和对话缓存，生成的 MCP 配置与 Goal 记录仍留在 `RSI_DATA_ROOT`。网络文件系统可能使 Node 同步加载依赖超过 MCP 初始化时限；将共享目录设在本机磁盘可以避免这类启动超时。共享 profile 的首次安装由文件锁串行化。

## 冻结与存储

数据根目录 `RSI_DATA_ROOT`（默认 `.rsi/local`）下：

- `goals/`：当前 Goal/revision 的状态与澄清记录。
- `goal-contracts/`：确认时冻结的不可变契约、基线、任务、模型、Runtime、Verifier 与研究 Skill 身份。
- `goal-revisions/`：旧 revision；`goal-events/`：有版本的事件。
- `goal-workers/`：独占 OS 锁、进程身份、控制请求、消耗账本与日志。
- `run-states/`、`runs/`：复用的实验及阶段/任务检查点，最终选版与评测在 `runs/RUN_ID/final/`。
- `content/`：带摘要校验的内容仓库，包含基线、候选、Skill 快照、轨迹及截图。
- `goal-outcomes/`、`skill-captures/`：生产结论与独立的提炼预算记录。

默认一条研究线，明确设置 research_agent_count 才多线。轮数、时间、有限重试均受契约约束。暂停取消当前工作；恢复复用已保存任务、提案、构建及选版。未落盘的本地操作允许重执行，远端任务收到稳定 execution_key。修改范围通过候选快照路径检查；模型与非目标资产由版本契约固定。

TRAIN 可进入研究工作区；VALIDATE 仅传聚合结果用于选版。达到开发条件后先落盘冻结最终候选，再评测独立 FINAL 基线和候选。开发条件包含相对于开发基线的增益要求；达到绝对质量但尚未满足所需增益时继续消耗允许的研究预算。最终失败不继续搜索；新 revision 需要与该 Goal 所有已暴露 FINAL 输入均不重复的最终评测集，重命名任务或修改不影响输入的种子不能绕过。精确 Token/费用/人工耗时不可观测时为 null，不能宣称费用预算已精确执行。

## Research Skills

```json
{"RSI_RESEARCH_SKILLS": {
  "library_root": "~/.areal_rsi/skills",
  "paths": ["~/.areal_rsi/skills/local-state/1.0.0/package"],
  "selection": "explicit", "capture_candidates": true
}}
```

路径支持 `~`；相对路径相对于启动 CLI 的工作目录，Goal 示例 runner 会转成绝对路径。Goal 的 skill_paths 未提供时继承机器默认；显式 `[]` 表示不加载。确认后从内容快照恢复，源目录改动或删除不影响已确认身份。

Skill 包必须含 YAML frontmatter 的 SKILL.md：name、version（semver）、description、scope、dependencies。只接受 SKILL.md、scripts/、references/、templates/，限 2 MB，拒绝符号链接和凭证。不会运行安装脚本。提案会话收到 Skill 入口和只读资源目录；选版不注入生产 Skill。当前只允许 DSH 声明研究 Skill 能力，其它适配器明确拒绝。

```bash
uv run rsi skill list
uv run rsi skill capture --goal GOAL_ID --max-seconds 600
uv run rsi skill inspect SKILL_ID --version 1.0.0
uv run rsi skill validate SKILL_ID --version 1.0.0
uv run rsi skill activate SKILL_ID --version 1.0.0
uv run rsi skill deprecate SKILL_ID --version 1.0.0 --reason "记录适用边界或回归"
```

Artifact 结束后按配置自动提炼候选，只提供开发改动和聚合证据，不提供最终验收明细。提炼另记时间与预算，失败不改变产物结论。候选不会自动激活。提炼可在工作区写文件并返回 workspace_files 清单，避免再次输出全部源码；只打包清单指定的文件，路径逃逸与源目录符号链接会被拒绝。验证会在隔离工作区执行 scripts/validate.py 的场景自测，并记录输出；结构检查或编译通过不能单独声明资源运行有效。同名更新分配新版本，旧包不覆盖。跨任务收益另做配对对照，不能从“已配置”或“已加载”推断有效。

Model 的异步任务契约在 api/training.py，覆盖提交、状态、取消、恢复、资源及父版本/checkpoint 关系；只运行模拟后端契约测试，不提供真实训练后端。

在缺少中文字库的 Linux 环境中，中文面板截图可能显示方框；浏览器验收环境应安装 Noto Sans CJK SC 或同等中文字库。实际 demo 字库身份记录在 examples/goals/results/browser-font.json，不影响功能检查的判定。

已安装 Playwright Python 包但缺少 Chromium 时，Goal 标记 blocked，保留安装或恢复 PLAYWRIGHT_BROWSERS_PATH 的提示。依赖恢复后使用同一 Goal 的 resume，沿用 revision、冻结契约和已提交检查点。真实 CLI 的阻塞到恢复证据见 examples/goals/results/browser-dependency-recovery.json。

Run 执行结束时显式释放 OS 锁，避免重复或继承的文件描述符延续已结束执行的锁；同一 Run 的其他实际持有者仍被互斥检查拒绝。相关暂停恢复与锁回归验证见 examples/goals/results/run-lock-regression.json。
