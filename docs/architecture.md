# 单 Agent 内核

```text
ui/dsh 原版组件 ← main.mjs ← ui/tui.py、ui/cli.py
        │ 调用；消费 SessionEvent
        ▼
infra/research.py · ResearchSystem
        ├── agents.py                  当前角色指令
        ├── coordination/store.py      原子状态、会话占用锁、UI 记录
        ├── coordination/policies/goal.py  Goal 继续/完成/等待/预算
        ├── coordination/tools/control.py  三个 Agent 控制工具
        ├── experience.py              本地文档与独立总结
        └── codex.py                   原生 app-server JSON-RPC
                └── Codex             模型、工具、历史、压缩、沙箱
```

`api/` 定义具名数据契约和协议，不依赖实现；`infra/` 不依赖 UI。
主会话没有 Researcher、独立 verifier、版本管理、memory、训练或 legacy 内核。
独立 `examples/tir` 使用 `infra/execution.py` 的子任务执行、`infra/training.py` 的 AReaL 接入、
`infra/coordination/evaluation/` 的评测与 `infra/trajectories.py` 的轨迹索引；
数据、流程和 TIR 策略保留在 example，尚未注册到会话控制工具。
API 中其余未来契约保留，当前会话运行不会实例化它们。

## 一次会话

1. `create(config)` 只保存普通会话。`open(run_id)` 连接独立配置目录中的 Codex。
2. `send_message()` 开始原生 turn；执行中的补充消息使用 `turn/steer`。
3. `/rsi` 等当前 turn 结束，在**同一个 thread** 切为 Interaction，继续讨论。
4. Interaction 用 `rsi_set_task` 保存 `TaskBrief`。用户要求执行时，调用
   `rsi_start_goal`；用户也能通过 `/start` 交接已准备的任务。
5. 当前 turn 完整结束后，`thread/fork` 复制原生历史给 Leader。
   Leader 是唯一执行者，自己读代码、修改、调用工具和验证。
6. Leader 用 `rsi_update_goal` 保存进展。若 turn 结束但目标仍 active，宿主启动下一 turn。
7. `/chat` 在当前 Leader thread 回到普通工作，保留刚完成的工作上下文。

角色是同一 Codex 内核的不同阶段，没有额外的 Agent 转发层。执行角色提示词放在
`infra/agents.py`，总结角色在 `infra/experience.py`，宿主续行/恢复提示词在 `ResearchSystem` 中。

新会话默认 `danger-full-access` / `never`：工作目录是起始目录，不是读写边界；操作受当前系统用户权限约束。
所有阶段和总结继承同一配置，旧会话恢复时仍使用其已保存权限。角色职责由提示词及 RSI 控制工具授权表达。

## 上下文归属

Codex 保存用户和助手消息、工具调用及结果、图片输入和压缩记录。RSI 不重建消息数组，
不截取最后几条记录，不用任务配置或 UI transcript 替代原生历史。
角色指令通过每个 turn 的 application context 更新；原生 Codex 基础指令保留。
Leader fork 是一个新 thread，但保留来源会话已有的上下文；后续工作继续在这个分支上。

这仍受模型上下文窗口限制。长会话使用 Codex 自己的压缩结果，并不意味着所有原文、
工具输出、图片像素会永远同时留在模型输入中。原始文件/图片路径和关键业务约束应写入
任务配置，便于需要时重新读取。RSI 没有实现第二套会话压缩；经验文档是跨任务的通用方法，不替代原生上下文。

## Goal 的停止条件

- Leader 提交 `completed` 和非空验证证据，且当前 turn 正常结束：完成。
- Leader 提交 `blocked` 和问题：等待用户；回答后继续。
- 用户 `/pause`、`/stop` 或退出：中断/保存，不偷偷重新启动。
- 用户设置的 token 或执行时间预算耗尽：停止；调整或取消预算后再 `/resume`。
- 原生 turn 失败或断线：保存不确定的执行状态，等待显式恢复。
- 连续两次自动续行均未使用工作工具：等待用户，避免空转。

新目标默认不设 token 和时间预算。Interaction 只保存任务，预算由用户通过 `/budget` 控制；
调整预算保留已用量，解除 `budget_limited` 后进入暂停状态，等待显式 `/resume`。
token 用量从 Leader 开始计算，使用 Codex 上报的累计用量差值，包含输入上下文。
它是收到用量通知后的中断上限，单次模型调用可能超出剩余额度。
`max_wall_seconds` 在本版计 Leader 活跃执行秒数，排除暂停和等待用户的时间。
完成证据由 Leader 提交，宿主校验结构；本版没有独立验证器替模型判断质量。

## 恢复与持久化

```text
RSI_DATA_ROOT/
├── codex/                        独立 CODEX_HOME；原生历史及配置
├── experience/<topic>.md         每个主题一份文档，直接更新
├── experience.lock               总结写入锁，不放入经验目录
└── sessions/<run_id>/
    ├── session.json              唯一生命周期状态；原子替换
    ├── owner.lock                同一会话仅一个宿主
    ├── transcript.jsonl          UI 展示，不作为模型上下文
    ├── learning.log              独立总结进程诊断
    └── codex.log                 原生进程诊断
```

投递前记录输入意图；工具控制结果先落盘再回复。重复控制调用按 call ID 复用回执，
改变参数则拒绝。恢复时核对原生最后一个 turn 的结果；投递结果不明确时，不自动重放命令。
`/resume` 会让 Agent 先检查历史、文件和进程，再继续未完成工作。这不能保证任意外部副作用
恰好执行一次，但避免宿主在不知道结果时盲目重试。

后续接入其他 UI 时复用 `ResearchSystem` 的控制方法和事件流。
本地经验自进化通过独立总结会话实现；Yuque 同步尚未实现。
`/learn-mode` 选择完成后自动总结或等待人工复盘；等待状态不占用模型，且不会改变任务已完成的状态。
总结会话收到最新目录和上次总结主题的最新正文；模型按适用场景与方法选择合并或新增。
同主题输出去重后的完整正文，保留 slug；完全相同的内容不重复写入。正文硬上限 4000 字符。
经验目录只保存 `experience/<主题>.md` 普通文件，不使用历史版本、哈希名称或软链接。
普通工作与 Leader 每个新回合刷新经验目录，按需读取最新正文；旧对话内容仍保留。
Interaction 不接收新的经验目录，且不能把经验建议写成用户约束。此前对话中的原始业务上下文仍完整保留。
保存时加锁并比较总结开始时的原文，防止并发总结覆盖其他修改；冲突需要重新总结。
写入临时文件后原子替换正文，锁和临时文件留在数据根目录，不混入经验目录。

执行期间持有系统闲置休眠抑制，停止/等待时释放。目标续行参考
[Codex Goals](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex)，
休眠范围参考原生 Codex 的 sleep-inhibitor；本版没有后台守护进程或跨机器恢复。
