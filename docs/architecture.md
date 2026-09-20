# ResearchApp 架构

ResearchApp 是唯一的研究应用入口。CLI、Web 工作台和仓库启动脚本都调用它。

```text
用户 → Web / rsi research → ResearchApp
                            ├─ Interaction：确认需求和执行配置
                            ├─ ResearchEngine / PassKEngine：调度消息与研究回合
                            ├─ ResearchRuntime → PackageAgentRuntime → DSH / Codex / Hermes 等
                            └─ ResearchExecution → JobManager → 实验命令与可信验证器
```

## 编排与执行

Interaction、RSI 和 Research Agents 的角色 prompt 位于 `agents/`，均由同一个
`ResearchRuntime` 执行。所有新研究都使用 pass@k，固定启动 k 名独立研究员；
纯 pass@k 中 RSI 只监督，leader guidance 模块增加证据共享和非阻塞建议，
不增加、替换或停止研究员。它们共用消息、节点、作业和验证记录，探索停止后
由系统执行最终复测，不存在另一套固定轮次实验调度器。

原生 Runtime 负责推理、工具调用、会话历史、压缩和供应商重试。ResearchEngine
负责收件箱交付、并发、时限、token 使用量、等待和取消。等待外部作业时不调用模型。

## 消息与会话

Interaction 在启动前确认任务和执行条件；RSI 的监督或建议范围由所选[协作模块](collaboration.md)
决定。每名研究员保留独立工作区和原生会话，在固定预算内持续探索；报告不会要求它等待
新的分工指令。预算、停止和继续规则以 [pass@k](passk.md) 为准。

消息、报告及作业事件先持久化，再触发调度。消息带稳定 ID、发送者、接收者、正文与引用；
重复交付复用已有回执，结果不明的外部操作先核查，避免重复提交。
角色 Prompt 保持稳定，预算和当前状态作为回合消息提供；上下文压缩由原生后端负责。
运行中建议在 RSI 工具返回或下一回合投递，不保证插入任意原生工具调用之间。

## 候选与验证

研究员在独立工作区修改产物，通过 `save_candidate` 创建内容寻址快照。
`verify_candidate` 将该快照复制到实验拥有的输入目录，由操作员登记的命令评测。
命令和答案不从候选目录导入；验证结果须匹配候选摘要、研究契约、评测协议、数据
身份及 phase。stdout 和研究员报告不能充当正式分数。

数学和 LCB 的适配器位于 `adapters/benchmark_research.py`。它只处理一次候选评测：
数学调用固定 Task 模型与 ArithmeticVerifier；LCB 复用固定 EnvArena
Benchmark、Model、Harness 和 Reward。研究编排始终属于 ResearchApp。

KernelBench 单题直接生成 kernel，由 GPUQ 执行正式评测；已有通用 KernelBench
harness 适配器也通过 ResearchApp 的命令和验证器接入。

候选可以通过 `withdraw_candidate` 撤回，保留原因、证据和原分数；失效候选不再参与
选优，纯 pass@k 的撤回仅影响当前尝试。最终复测不能用于继续优化。
实验命令的自动验证、收尾选优和输出协议见[接口](interfaces/README.md#实验命令与评分)。

## 数据

`api/base.py` 定义 `ResearchPlan`、`ResearchRun`、Agent 与节点记录，以及独立 Task
评测使用的模型、能力快照、轨迹和验证契约。`api/research.py` 定义需求、候选、
研究消息、报告与可信成绩。

```text
<data_root>/runs/<run_id>/
├─ run.json
├─ coordination/         # 调度、配置、作业意图
├─ agents/<id>/          # 工作区与原生会话
├─ iterations/           # 研究节点
├─ candidates/           # 内容寻址产物记录
├─ jobs/                 # 进程、日志、状态
├─ execution-inputs/     # 冻结的验证输入
├─ execution-outputs/    # 私有结果与显式公开反馈
├─ verifications/        # 已核验分数
└─ finalization/         # 可选的操作员最终复测
```

每个研究由执行锁限制为单个调度器。当前格式的暂停、重启和消息去重保留；不迁移
已删除入口的计划、检查点或旧字段。旧实验需使用原版本，新实验使用当前配置。

## 代码位置

- `research_app.py` 与 `ui/workbench*`：应用和工作台入口。
- `coordination/`：需求接入、协作选择、消息、调度与候选验证。
- `agents/`：角色 Prompt；`modules/research_runtime.py`：原生运行时适配。
- `execution/`：后台进程、资源和作业；`adapters/`、`examples/`：任务接入。
- `api/`、`base/`：数据契约与可复用存储。优化对象分类和领域接口见[接口](interfaces/README.md)。
