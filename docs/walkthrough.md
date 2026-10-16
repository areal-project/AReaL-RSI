# 半小时读完当前内核

先沿一次真实会话阅读，再看异常分支。未来 API 契约和原样搬运的第三方 UI 不属于自己的执行内核。
这份路线覆盖所有自有模块；`ResearchSystem` 最长，因为生命周期、异步事件和中断必须在同一把锁下协调，
没有为了减少单文件行数额外拆出转发类。

## 0–4 分钟：状态和入口

读 `api/research.py` 开头的 `SessionConfig`、`SessionRecord`、`SessionInput`、`SessionEvent`。
一个 run 是一条工作会话；phase 决定当前角色，status 决定能否执行。
`pending_inputs` 是待投递消息；`in_flight` 记录已经尝试投递的消息；`active_turn_id` 标识正在执行的回合。
`TaskBrief` 是目标，不是对话历史；`GoalProgress` 是可公开的进展，不是模型思维记录。

读 `ui/cli.py`：加载项目 `.env`，选择 TUI、JSON 命令行或总结命令。
读 `infra/config.py`：解析连接参数、工作目录和独立数据目录。模型密钥不进入会话记录。

## 4–12 分钟：正常工作路径

读 `infra/research.py` 的以下路径：

1. `create` 保存普通会话及当前经验目录；不调用模型。
2. `open/_attach` 取得会话锁、启动原生 Codex、创建或恢复 native thread。
3. `send_message` 保存用户消息；空闲时排队，执行时用 native steer 补充要求。
4. `enter_rsi` 记录阶段切换，等当前 turn 结束；Interaction 使用同一条历史。
5. `start` 或 Interaction 的 `rsi_start_goal` 记录交接。
6. `_serve` 是唯一调度循环；`_drive` 只在空闲边界切换阶段、fork Leader、投递下一回合。
7. `_event` 将原生事件转成展示事件和具名状态；turn 结束后结算状态。

从这条路径看清楚：没有第二套 Agent 执行循环。模型、文件工具、命令执行、沙箱、历史、压缩都在 Codex 中。

## 12–17 分钟：目标为何继续、何时停止

读 `coordination/policies/goal.py`：预算检查、回合结算和能否继续，总共三个函数。
回合结束只是一次执行结束；目标仍 active 且无待处理输入时，宿主发起下一回合。
完成、等待用户、预算停止、暂停、失败都有明确状态；连续两次无工作自动回合会停止空转。

读 `coordination/tools/control.py`：三个控制工具及角色授权。
Interaction 保存任务和启动；Leader 更新进度、带证据完成或报告阻塞；普通聊天没有这些权限。
JSON 通过 `api/control.py` 统一校验。回执落盘防止同一个控制调用重复生效。

读 `agents.py`：HOST、CHAT、INTERACTION、LEADER 四段角色提示词。
任务、进展、预算通过每个回合的 application context 传入。恢复和继续提示词在 `ResearchSystem`；
不要把旧角色提示词或任务摘要当作新会话的完整历史。

## 17–22 分钟：中断与保活

读 `pause/resume/stop/chat/answer`，再看 `_interrupt` 和 `_detach`。
控制方法接收 run ID，校验状态，在同一会话锁下修改状态。
暂停先记录意图再中断。原生中断超过五秒未确认时关闭宿主拥有的进程，保留不确定输入；
恢复先查历史/已有结果，不能盲目重放有副作用的工作。

读 `codex.py`：一个 JSON-RPC 子进程连接，按请求 ID 配对响应，原生通知放入队列。
执行或整理经验时持有系统闲置休眠抑制；没有活动任务后释放。它不能抵抗关机、合盖强制睡眠或进程被杀。

读 `coordination/store.py`：session 原子替换、同一 run 的进程锁、展示 transcript。
transcript 用于恢复界面；模型仍使用 Codex 原生历史。

## 22–26 分钟：经验如何积累

读 `experience.py`：文档目录、按需读取、独立总结、保存四件事。
RSI 完成后按 `/learn-mode auto|guided` 自动总结或等待人工复盘；普通工作或暂停任务可 `/learn`。
总结从已结束回合 fork，继承原会话权限，
输出具名文档草稿；宿主检查命名、篇幅和并发修改，再原子替换该主题的 Markdown 文件。
重复触发复用结果；总结失败不撤销任务完成，用户可重试。

普通工作/Leader 只拿文档目录和主题文件路径，按需读取内容；不会继承来源任务的对话或解题代码。
Interaction 只整理需求，不注入新的经验目录，也不能把经验建议变成实现约束。
每个新回合刷新目录，读取最新正文；经验库不保留历史版本。云同步暂未实现，避免提前引入空接口。
CURATOR 提示词在此文件；不保存题目答案、私密数据或具体实现代码。

## 26–30 分钟：UI 与验证

读 `ui/tui.py`：启动 Node、按锁文件安装依赖、JSON 输入转换为 ResearchSystem 方法，输出事件。
读 `ui/dsh/main.mjs`：把事件交给原版 DSH 组件，把键盘/问答交给 Python；它不管理目标或模型历史。
上游来源和许可证见 `docs/tui-comparison.md`。

最后看 `tests/test_codex_native.py`：用真实 Codex 接离线模型 fixture，直接检查模型实际收到的业务文字、
工具结果、图片和角色。`tests/test_research.py` 覆盖中断、恢复、授权、预算和自动总结故障。
`tests/test_tui.py` 从真实终端输入走完整链路；`tests/experience_e2e` 是显式付费的真实 API 实验。
