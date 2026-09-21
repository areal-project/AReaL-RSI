# ResearchSystem 架构

ResearchSystem 是研究系统的核心控制入口，直接实现在 `infra/research.py`。
CLI 和 Web 工作台调用同一个系统；系统本身不依赖 HTTP 服务或浏览器。

```text
用户 → Web / rsi research → ResearchSystem
                            ├─ Interaction：确认需求和执行配置
                            ├─ ResearchEngine / PassKEngine：调度消息与研究回合
                            ├─ ResearchRuntime → PackageAgentRuntime → DSH / Codex / Hermes 等
                            └─ ResearchExecution → JobManager → 实验命令与可信验证器
```

## 源码结构

```text
areal_rsi/
├── api/                       # 数据契约、组件协议、控制请求
├── infra/                     # 核心实现
│   ├── research.py            # ResearchSystem 生命周期与控制
│   ├── config.py              # 模型连接设置
│   ├── storage/               # 文件存储、快照、运行记录
│   ├── coordination/          # 状态机、调度；policies/tools/evaluation 子包
│   ├── execution/             # 进程、队列、GPU 资源
│   ├── runtime/               # 推理后端、隔离、worker、插件
│   ├── tasks/                 # Task 执行、评测、产物和远程组件
│   ├── adapters/              # 具体评测接入研究流程
│   └── agents/                # 角色提示词
├── ui/                        # 可选 CLI/HTTP/浏览器入口
├── default_harness/           # 默认能力模板资源
└── resources/                 # 固定 Runtime 包与锁文件
```

`base` 原来包含文件读写、快照和运行索引，属于实现而不是 API；已归入 `infra/storage`。
纯定义放在 `api`，包括原执行模块的 JobSpec/JobRecord/ApprovedCommand。
这里的 `infra` 表示核心实现集合，其内部仍按状态和资源的归属分工。

## 编排与执行

Interaction、RSI 和 Research Agents 的角色 prompt 位于 `infra/agents/`，均由同一个
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

数学和 LCB 的适配器位于 `infra/adapters/benchmark_research.py`。它只处理一次候选评测：
数学调用固定 Task 模型与 ArithmeticVerifier；LCB 复用固定 EnvArena
Benchmark、Model、Harness 和 Reward。研究编排始终属于 ResearchSystem。

KernelBench 单题直接生成 kernel，由 GPUQ 执行正式评测；已有通用 KernelBench
harness 适配器也通过 ResearchSystem 的命令和验证器接入。

候选可以通过 `withdraw_candidate` 撤回，保留原因、证据和原分数；失效候选不再参与
选优，纯 pass@k 的撤回仅影响当前尝试。最终复测不能用于继续优化。
实验命令的自动验证、收尾选优和输出协议见[接口](interfaces/README.md#实验命令与评分)。

## 数据

`api/records.py` 定义 `ResearchPlan`、`ResearchRun`、Agent 与节点记录，以及独立 Task
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

## 模块职责与划分判断

以下是当前生产模块清单。判断依据是状态/资源归谁拥有、是否存在独立运行边界，
而不是文件长短。表中路径相对于 `src/areal_rsi/`；各目录的 `__init__.py` 仅标记包
或集中公开符号，没有独立业务职责。`__main__.py` 只连接 CLI 入口。

### 入口与契约：保留

| 模块 | 含义与边界 |
| --- | --- |
| `infra/research.py` | 组装研究、管理活跃 worker、处理操作员控制和导出。文件较大，但拥有同一个系统生命周期；不再为单个操作增加服务层。 |
| `infra/config.py` | 研究模型 `ResearchConnection` 与独立 Task 模型 `ModelConfig` 的连接参数。现在共享 URL 校验；不包含研究状态或命令调度。 |
| `api/records.py` | 内容/版本/任务/轨迹/验证等记录，以及 Run/Agent/Iteration 的基础状态。保留既有记录定义，避免纯整理引起格式迁移。 |
| `api/research.py` | 研究需求、预算、消息、报告、候选及可信成绩记录；与独立 Task 的验证结果不是同一对象。 |
| `api/interfaces.py` | 存储、任务执行、评测、训练和研究回合协议。集中定义；`ResearchAgentRuntime` 和 `TaskAgentRuntime` 各自有明确生命周期。 |
| `api/control.py` | 操作员 JSON 请求契约、入口校验、HTTP 暴露范围和下载记录；不承担业务状态迁移。 |

### 基础存储：保留，共享原子写入

| 模块 | 含义与边界 |
| --- | --- |
| `infra/storage/files.py` | CAS 内容、目录快照、不可变记录、版本、Run 内轨迹、轻量记忆；`write_bytes` 统一字节发布，`write_json` 负责编码。 |
| `infra/storage/runs.py` | Run 的目录、引用、Agent/Iteration 索引与记录重建；处理 Run 归属，不是另一套内容存储。 |
| `infra/storage/evidence.py` | 证据清单及按授权路径导出；与普通快照相比多了授权和路径冲突边界，不能直接并入无条件复制。 |
| `infra/storage/catalog.py` | 按内容复用模型和基线版本，有自己的索引锁；不同于 Benchmark 目录。 |
| `infra/storage/paths.py` | CLI、启动器、preflight 共用数据根目录优先级；虽短但已有多个使用者。 |

### 研究编排：保留职责边界

顶层保留 8 个业务文件：coordinator/engine（状态与调度）、effective_time（计时）、
config/intake/presets（配置与启动）、store/views（记录与只读视图）。其余按职责分组：

```text
coordination/
├── policies/     # collaboration、passk、leader_guidance：研究策略
├── tools/        # actions、brief、web：Agent 工具与输入校验
└── evaluation/   # jobs、verification：实验及可信评测
```

子包只组织代码，不新增调用层或管理器；数据目录仍是原来的 coordination/。
合并了 preparation → intake、sources → tools/web，没有删除实际研究能力。

| 模块 | 含义与边界 |
| --- | --- |
| `infra/coordination/coordinator.py` | 持久化研究状态机：角色、收件箱、分配、报告、暂停/继续；是研究状态操作的中心。 |
| `infra/coordination/engine.py` | 调度异步回合、限制并发、处理执行锁/取消/恢复；与持久化状态机分开，避免把进程内任务对象写进研究记录。 |
| `infra/coordination/tools/actions.py` | Agent 工具定义、授权、幂等回执、候选和文件操作；不直接实现模型推理。 |
| `infra/coordination/store.py` | 研究记录、候选快照、契约摘要、可信成绩和撤回选优；复用 storage，不管理模型回合。 |
| `infra/coordination/evaluation/jobs.py` | 把实验作业关联到研究员/节点/候选，冻结输入、发布反馈、触发验证与完成消息；底层进程由 execution 管理。 |
| `infra/coordination/evaluation/verification.py` | 校验操作员验证输出与冻结候选、契约、phase、数据身份一致；stdout 不构成正式分数。 |
| `infra/coordination/config.py` | 整个研究的操作员配置：需求、协作、命令、连接选择、冻结/加载；与 infra/config 的连接值对象范围不同。 |
| `infra/coordination/intake.py` | 启动前整理需求、接入环境、检查执行条件与用户确认；原 preparation 已合入，内置准备只有这一处调用。 |
| `infra/coordination/tools/brief.py` | update_brief 的 schema 与更新规则；工具定义和服务端校验共用。 |
| `infra/coordination/presets.py` | 开发/模拟研究的示例配置；不是调度策略。 |
| `infra/coordination/policies/collaboration.py` | 协作配方、依赖顺序、版本及冻结指纹；版本化策略入口。 |
| `infra/coordination/policies/passk.py` | 固定 k 个独立尝试的权限、继续与停止规则，扩展通用 actions/engine。 |
| `infra/coordination/policies/leader_guidance.py` | 在 pass@k 上增加有来源的定向建议和共享权限；保持独立尝试不变。 |
| `infra/coordination/effective_time.py` | 每名研究员的有效时间，扣除 API 故障/GPU 排队区间的并集；不依赖 UI。 |
| `infra/coordination/views.py` | 从同一记录构建模型上下文与操作员查询投影，不建立另一份可写状态。 |
| `infra/coordination/tools/web.py` | 网页检索、抓取、分页、地址检查，以及检索结果的证据登记；原 sources 已合入。 |

### 执行与资源：保留独立进程边界

`execution` 是底层作业执行器：启动登记命令、管理本地进程/队列与配置的 GPU 配额，
处理超时、取消、进程组回收和恢复。它不决定研究方向、选优，也不判断成绩。
`coordination/evaluation/jobs.py` 才把作业与候选/研究员关联；远端 GPUQ 的调度由对应适配器负责。
LLM 推理会话属于 runtime，不能把这些名字都叫“执行”就合并成一层。

| 模块 | 含义与边界 |
| --- | --- |
| `api/execution.py` | JobSpec、JobRecord、事件与操作员命令模板；执行对象不携带研究评分语义。 |
| `infra/execution/manager.py` | 作业队列、并发/GPU 资源、持久化状态与恢复核查。 |
| `infra/execution/_runner.py` | 脱离主进程的监督程序，处理超时/取消/子进程组回收；只依赖标准库，不能为了复用而导入系统包。 |
| `infra/execution/progress.py` | 有大小限制、字段筛选的公开作业进度，不生成可信成绩。 |
| `infra/execution/diagnostics.py` | 操作员发布的受限失败说明；不是原始私有日志读取器。 |
| `infra/execution/pinned_sources.py` | 可复制到远端的独立 Linux 挂载入口，按摘要固定操作员源码；需要保持可独立执行。 |

### 后端与 Task 组件：保留运行边界，合并安装/连接配置

| 模块 | 含义与边界 |
| --- | --- |
| `infra/runtime/research_runtime.py` | 研究消息、工具和事件与原生 Runtime 的桥接。它了解 ResearchConfig 是有意的适配职责，不应把所有 runtime 实现都视为完全不懂研究的底层库。 |
| `infra/runtime/runtime_package.py` | 多后端配置、隔离命令、PackageAgentRuntime 会话与 worker 生命周期。 |
| `infra/runtime/runtime_worker.py` | 子进程内装配上游 Runtime 并传递事件；与父进程分开是恢复和隔离要求。 |
| `infra/runtime/dsh_runtime.py` | 仍被独立 Task/KernelBench 使用的直接 DSH SDK 会话。与 package 路径的 SDK 实现及会话约束不同，不是废弃代码，不能直接删除。 |
| `infra/runtime/dsh.py` | 共用 DSH CLI 及匹配 profile 的安装、版本检查和锁；原 runtime_dsh 已合入。 |
| `infra/runtime/runtime_storage.py` | 原生 Runtime 的可恢复事件 journal；它存的是 Runtime 事件，不替代研究记录。 |
| `infra/runtime/runtime_tools.py` | 沙箱内 RSI 工具桥；由宿主授权每次调用，没有协调器写权限。 |
| `infra/runtime/runtime_mcp.py` | 候选 MCP 工具发现与调用插件，独立能力配置。 |
| `infra/runtime/runtime_workspace.py` | 显式授权的 Linux workspace 工具插件；不是默认开放宿主 shell。 |
| `infra/runtime/runtime_network.py` | macOS 沙箱内原生客户端的 HTTPS 通道，固定公网目标并保持端到端 TLS。 |
| `infra/runtime/file_reader.py` | 可复制进沙箱的只读、分页、白名单文件工具；不能合入有宿主存储访问权的模块。 |
| `infra/runtime/gateway.py` | 模型协议/SSE 适配、受控流式重试与审计；不拥有研究预算。 |
| `infra/tasks/execution.py` | 独立 Task 的工作区输出采集与目标模型请求预算；复用 gateway，不重写推理循环。 |
| `infra/tasks/harness.py` | Harness 文件校验、输入快照、MCP 装配和单 Task 执行。 |
| `infra/tasks/artifact.py` | 应用 patch 生成不可变产物，不执行候选或评测。 |
| `infra/tasks/arithmetic.py` | 原创算术题、其验证器与准确率聚合；不是通用 Benchmark 注册目录。 |
| `infra/tasks/gsm8k.py` | GSM8K 数据准备、分区身份、答案解析与验证。 |

`runtime_*.schema.json` 是对应插件的配置契约；插件按能力和进程边界独立加载，
不能因文件名相近就全部合成一个 runtime.py。

### 远程评测、评测目录与通用扩展：保留不同接入层

| 模块 | 含义与边界 |
| --- | --- |
| `infra/tasks/remote/envarena/client.py` | EnvArena HTTP、认证、重试、引用、上传下载与脱敏。 |
| `infra/tasks/remote/envarena/config.py` | 远程 Benchmark/Model/Harness、题目分区及允许修改范围的配置校验。 |
| `infra/tasks/remote/envarena/benchmark.py` | 远程题目身份、公开 Task 与结果验证/汇总。 |
| `infra/tasks/remote/envarena/harness.py` | 冻结远程 Harness、创建候选修改及发布版本的身份核查。 |
| `infra/tasks/remote/envarena/runtime.py` | 远程 Task 提交、重连、取消、产物与轨迹获取；与本地进程监督不同。 |
| `infra/tasks/catalog.py` | Benchmark 元数据、参数校验、注册表和内置装配；原 adapters.py 已合入，消除互相导入。 |
| `infra/adapters/benchmark_research.py` | 把数学/LCB 评测接入研究候选验证命令；区别于 catalog 的组件装配。 |
| `infra/adapters/kernelbench.py` | KernelBench 数据及独立目标 Task 的可信评测接入。 |
| `infra/tasks/verifier.py` | 可选 Python 验证器加载、超时与结果身份检查；不是 coordination 的候选文件验证协议。 |
| `infra/storage/trajectory.py` | 可配置存储提供方与可移植 file:// 轨迹包。与 storage/files 的 Run 内 CAS 轨迹格式不同，保留各自 API/磁盘格式，仅共用原子写入。 |

文件存储归 `infra/storage`，验证器实现归 `infra/tasks`，不另设 framework 层。
可移植存储命名为 `PortableTrajectoryStore`，与 Run 内的 `FileTrajectoryStore` 区分。

### Prompt、UI 与资源：保留

| 模块 | 含义与边界 |
| --- | --- |
| `infra/agents/communication.py` | 共同沟通/完整性约束和工具说明函数。 |
| `infra/agents/interaction/prompt.py` | 启动前需求对话角色说明。 |
| `infra/agents/research/passk_prompt.py`、`leader_guidance_prompt.py` | 独立研究员及有指导协作时的角色说明。 |
| `infra/agents/rsi/passk_prompt.py`、`leader_guidance_prompt.py`、`leader_guidance_v022_prompt.py` | RSI 监督/指导的冻结版本；不可为去重改写历史版本内容。 |
| `ui/cli.py` | 命令行参数、研究/工作台启动与中断退出。 |
| `ui/workbench.py` | 本地 HTTP、安全校验、公开响应投影与下载传输，不拥有研究状态。 |
| `ui/launch.py` | 检测或启动本地服务、脚本化访问同一系统。 |
| `ui/preflight.py` | 用真实后端验证工具、会话继续和重启。 |
| `ui/workbench_static/` | 浏览器交互、图谱布局、样式和静态页面，不是另一套后端。 |
| `default_harness/` | 默认 prompt、技能、MCP 配置；`tools/math_mcp.py` 提供计算工具，`tools/check.py` 是只允许算术 AST 的表达式执行脚本。 |
| `resources/runtime/` | 固定的 DSH Runtime 包、来源信息和依赖锁，不是业务模块。 |
| `infra/tasks/config/` | 支持的评测及公开参数清单，不含答案或任意动态代码入口。 |

## 结果完成与作业数据

`finalization.py` 已删除。研究结束时，ResearchSystem 只组织候选选择和可选验证；
输入冻结、作业登记、等待及结果核验统一由 ResearchExecution 处理。
执行锁与同一个 JobManager 持续持有到结果处理结束，不再在两个执行器之间交接。

`api/research.py` 的 ResearchJob、JobWaiter、CompletionRecord 分别定义作业关联、
完成订阅和结果处理状态。JSON 字典只在存储/对外投影处转换，作业索引由 ResearchStore
统一编解码；旧 jobs.json 和 finalization 字段在读取时适配，不因读取而写回迁移。
`Coordinator` 负责候选选择及开始、中断、失败、完成状态迁移。

结束阶段成功处理结果记为 completed；若明确的数值 target 达标则记为 succeeded，是否达标另存
`CompletionRecord.target_reached`。没有 target 时该字段为 null，不再把正常完成记为取消。
尚未确认释放资源的 lost 作业不能启动下一次尝试；暂停服务会保留已启动的结果评测，
恢复时复用原作业身份。用户明确停止则取消作业，并保留无法确认的资源声明。

## 迁移与保留原则

- `ResearchApp` / `research_app.py` 已移除，统一使用 `areal_rsi.infra.research.ResearchSystem`。
- `api.base` → `api.records`，`api.modules` → `api.interfaces`，`api.application` → `api.control`。
- 原 `base` → `infra.storage`；原 coordination/execution/agents/adapters 均归入 infra。
- 原 modules 的后端实现归入 `infra.runtime`，独立 Task/评测组件及原 benchmarks 合入 `infra.tasks`。
- 仓库内调用、CLI、Runtime worker 复制路径、插件入口、资源定位与沙箱限制同步调整，不留转发壳。
- 外部 Python 调用方需要更新导入路径。已有研究的数据目录与 JSON 字段不随源码目录移动。
  冻结 source-lock 保持原字节；旧版本重现实验仍须使用匹配的源码版本，不能自动改写清单。
  归档命令若包含旧模块路径，应以原版本恢复，或新建使用新路径的研究；不自动改写冻结命令。

不新增调度器、持久化机制或服务框架。`tests/test_architecture.py` 检查 API/infra/UI 边界，
已有行为测试覆盖核心流程；统一运行 `make check`。公开接口见[接口文档](interfaces/README.md)。

### 内部状态与 JSON 边界

`api/research.py` 的 `CoordinationState` 定义运行调度状态；需求确认、回合失败与重试、
报告摘要、Agent 活动和有效计时均使用具名记录。Coordinator 打开记录时解码，
保存时统一编码，业务代码使用属性访问；按 Agent/作业 ID 查询的字典保留，值有明确类型。
旧记录缺省字段由数据契约补齐，读取不会写回磁盘。计时区间仍序列化为原来的二元素数组。

工具输入、外部协议、插件配置和 UI 投影允许 JSON；这些数据不应反过来成为内部状态模型。
例如待回复报告先形成 `PendingReportReply`，只有输出给 UI 时才编码；作业通知队列直接
保存 `JobEvent` 对象。新增固定字段应先更新契约，再通过 Mypy 检查所有调用点，不能以
`Any`、`get()` 默认值或字典兼容包装绕过约束。
