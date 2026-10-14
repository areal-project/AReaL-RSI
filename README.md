# AReaL RSI

一个持续执行目标的研究系统。默认 `goal` 模式由 **Leader 亲自执行**调研、修改、实验和验收；一轮模型回复结束后，目标未完成就继续。Interaction 暂时保留，负责启动前整理需求。交互入口为 TUI。

系统只有两种模式：

| 模式 | 执行方式 | 完成责任 |
| --- | --- | --- |
| `goal`（默认） | 一个 Leader，不创建 Researcher | Leader 根据本 run 的验收条件与证据完成任务 |
| `legacy_passk` | 当前 main 的 v0.1：固定 k 个 Researcher，各自探索完整任务 | 监督者维持独立尝试，系统按目标或预算停止并执行配置的最终验证 |

未来 Agent Team 由 Leader 分解任务并启动 Researcher，Researcher 只完成分派任务，Leader 负责整合和本 run 准出。目前仅保留契约，不提供可运行的 team 模式。旧协作版本、Web 工作台和历史实验启动器已删除，旧 run/config 不迁移。

## 启动

需要 Python 3.11+、uv；standard 后端还需要 Node.js 22.19+ 和 pnpm。

```bash
uv sync --locked
cp -n .env.example .env
# 编辑 .env，填写 RSI_BASE_URL、RSI_API_KEY、RSI_MODEL 等模型连接配置
uv run --env-file .env --locked rsi tui
```

也可以运行 `uv run --env-file .env python run.py`。在终端输入目标，与 Interaction 补齐可检查的要求后交给 Leader。已经准备好配置时，执行以下命令，然后在 TUI 中输入 `/start`：

```bash
uv run --env-file .env --locked rsi tui --config examples/goal.json
```

研究已有 Git 项目：

```bash
uv run --env-file .env --locked rsi tui --workspace /absolute/path/to/repository
```

Leader 从启动时的 HEAD 创建独立 worktree；用户检出目录中的未提交修改不会带入。工作区路径显示在 TUI 中。普通材料可以通过配置 `seed_dir` 复制到 Leader 工作区。

## 交互和恢复

| 输入 | 行为 |
| --- | --- |
| 普通文字 | 启动前交给 Interaction；运行时直接补充给 Leader |
| `/start` | 按已保存需求开始执行 |
| `/pause`、Ctrl+P | 暂停模型执行；已提交作业继续受管理 |
| `/resume` | 恢复暂停任务；预算耗尽不能直接恢复 |
| `/retry` | 重试失败的当前角色，复用原任务及会话 |
| `/stop` | 终止当前 run 并取消受管理的未完成作业 |
| `/export /new/path` | 将已验收的不可变候选导出到新目录 |
| `/new` | 当前任务结束后创建新任务 |
| `/quit`、Ctrl+Q | 保存退出；保留任务状态和作业，供恢复 |

重新打开同一 run：

```bash
uv run --env-file .env --locked rsi tui --resume <run_id>
```

数据目录优先级为 `--data-root`、`RSI_DATA_ROOT`、`.rsi/research-lab`。恢复时使用原数据目录。TUI 每次管理一个 run，同一 run 只允许一个进程控制；已经完成的 run 可查看和导出，追加工作应创建新 run。

无交互运行和后端检查：

```bash
uv run --env-file .env --locked rsi research --config examples/goal.json
uv run --env-file .env --locked rsi preflight
```

无交互执行输出 JSON 事件；等待用户输入时退出码为 3，并打印 TUI 恢复命令。没有配置正式 verifier 的通用任务可以由 Leader 基于测试和产物证据完成。有 verifier 时，系统要求同一候选的正式验证满足协议，不能用文字声明或其他候选的成绩替代。

## 开发

```bash
make check
```

核心入口是 `infra/research.py` 的 `ResearchSystem`；`api/` 定义契约，`infra/` 实现运行、存储、评测和编排，`ui/` 只负责展示和输入。详见[架构与后续 Team 设计](docs/architecture.md)、[接口](docs/interfaces/README.md)、[legacy pass@k](docs/passk.md)、[后端](docs/agent-backends.md)。
