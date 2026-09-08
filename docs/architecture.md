# Python RSI Infra 架构边界

## 1. 组件与数据流

```text
┌──────────────────── User Interface ────────────────────┐
│ DSH TUI ⇄ RSI MCP ⇄ ExperimentController              │
│ CLI / 只读 Web：实验操作与状态展示                    │
└──────────────────────────┬─────────────────────────────┘
                           ▼
┌──────────────────────── Evolver ───────────────────────┐
│ 固定 Python Workflow                                   │
│ ├── RSI Agent：观察 Run，剪枝 ResearchAgent            │
│ ├── ResearchAgent：分析、修改、选版                     │
│ └── Task Agent：执行 Benchmark Task                    │
└──────────────────────────┬─────────────────────────────┘
                           ▼
┌────────────────────── Base Module ─────────────────────┐
│ AgentRuntime / HarnessRuntime / Benchmark              │
│ Verifier / Evaluation / Memory / TrajectoryStore       │
│ VersionManager / ContentStore                          │
│ HarnessBuilder / TrainingBackend / ArtifactBuilder     │
└────────────────────────────────────────────────────────┘
```

本地 AgentRuntime 由 `PackageAgentRuntime` 接入 ARealAgentRuntime，按角色选择 DSH、Codex 等 Harness；已有原创算术与 GSM8K Benchmark，KernelBench 暂缓。Remote mode 由选定 backend 执行 Task 和 Reward，EnvArena 是其中一种实现。本地研究运行时与远端任务运行时独立。核心 API 不出现具体框架或 Benchmark 名称。

三层边界与设计文档一致：User Interface 负责对话和实验控制；Evolver 由 RSI Agent、ResearchAgent、Task 执行和固定 Workflow 组成；Base Module 提供运行、存储、版本、训练、构建与评测能力。AgentSpec 显式列出 Runtime、Model、Prompt Template、Skill、Tool 和 MCP 依赖，避免把组合关系隐藏在不透明配置文件中。

默认对话入口由 DSH 原生 TUI 持有 Session 和 Agent loop，通过本地 MCP 生成计划、查询状态并控制实验。`ExperimentController` 根据用户目标、轮数和本地固定配置生成计划；用户确认后启动独立 worker，worker 装配既有 `ExperimentService` 和 Python Workflow。退出对话不会终止已提交的实验。

后台在隔离 Python 进程中通过 Runtime package 的公开 API 管理 Session、工具和事件。
使用 DSH 时，前后台复用固定版本 CLI；前台加载 TUI profile，后台使用包含 Runtime bundle
的 `agent-runtime` profile，共享依赖只读，各 Session 的可写工作区独立。
其他 Harness 通过 `RSI_RUNTIME_CONFIG` 选择，详见 [Runtime package](runtime-package.md)。

DSH Session ID 标识对话，RSI Run ID 标识实验。后台进程使用文件锁和内核进程启动身份避免重复启动或误停；状态经 MCP 返回有界聚合摘要，详细进度由只读 Web 面板展示。

Interaction Agent、RSI Agent 和 ResearchAgent 组分别解析模型连接，默认使用 teacher；`RSI_AGENT_MODELS` 可覆盖这三个角色。所有 ResearchAgent 共用 `research-agent` 的有效连接，不支持单条研究线覆盖。Task Agent 是 target 的 Harness 执行器，local 使用 `RSI_TARGET_*`，remote 使用 backend 配置的 Model 引用，不再定义第二份模型配置。相同的完整连接配置可复用代理；不同模型和认证信息分别路由。`SolutionVersion.model` 表示 target，计划的 `config.agent_models` 只保存 RSI Agent 和各 ResearchAgent 的模型引用，target 随基线 Solution 记录。Interaction Agent 模型单独记录，不约束后台 Run 的恢复。

Local 数据目录不绑定某个模型。模型描述按内容复用，基线按 target 模型与初始 Harness 快照索引；改变研究 Agent 或Interaction Agent 模型不创建另一份目标基线，同一目录可以保存不同 target 的实验。旧 `baseline.json` 只作兼容读取，不覆盖。每个 Run 继续引用自己的不可变 Solution，恢复时核对对应配置。

## 2. 独立研究线

```text
ExperimentRun
├── ResearchAgent 1
│   ├── Iteration 1：S0 → S1-1
│   └── Iteration 2：S1-1 → S1-2
└── ResearchAgent 2
    ├── Iteration 1：S0 → S2-1
    └── Iteration 2：S2-1 → S2-2
```

当前 Harness / Artifact 工作流中，每个 ResearchAgent 从同一 baseline 出发，独立维护 iteration。每轮只修改一个目标，只产生一个候选。并行性来自多个 ResearchAgent，而不是单个 Agent 一次提出多个修改。这是具体工作流的约定；通用 ResearchIteration 只记录任务、状态、报告及材料引用，不要求产出候选或完成评测。

RSI Agent 在外层读取 ExperimentRun，返回需要停止的 ResearchAgent ID，对广度搜索做剪枝；ResearchAgent 在各自研究线内部通过 select 做深度搜索和回溯。

## 3. 最小版本模型

```text
ModelVersion + HarnessVersion + ArtifactVersion[] = SolutionVersion
```

- ModelVersion：一个模型快照；Solution 中引用的是被评测的 target 模型；
- HarnessVersion：可复现的 Harness 快照。Local 保存目录；remote 保存远端配置与固定资源引用，Runtime 由平台提供；
- ArtifactVersion：用户产物的一个快照，例如游戏、应用或生成的数据；
- SolutionVersion：一次实际运行和评测使用的 Model/Harness/Artifact 组合。

版本只记录父版本。版本由哪个 Run、ResearchAgent 和 iteration 产生，可从节点关联的工作流结果追溯。ResearchIteration 不再直接保存候选和评测字段；当前流程使用 EvolutionResult 承载这些字段，通过版本 ID 关联 SolutionVersion。EvolutionResult 是 workflow 层可选择使用的结果格式，通用节点无需生成它；其他工作流可以采用自己的报告与材料记录。后续单独演进 Solution 模型。

## 4. 一轮研究

```text
TRAIN Tasks
    ↓ HarnessRuntime
TaskResult（output + trajectory）
    ↓ 组装 VerificationRequest
用户 Verifier
VerificationResult
    ↓ Evaluation
EvaluationReport
    ↓
Workflow 收集独立证据文件，ResearchAgent 按授权路径用工具读取，提出一个 Change
    ├── HarnessChange → HarnessBuilder
    ├── ModelChange   → TrainingBackend
    └── ArtifactChange → ArtifactBuilder
    ↓
新 ModelVersion、HarnessVersion 或 ArtifactVersion
    ↓
新 SolutionVersion
    ↓ VALIDATE
ResearchAgent.select(历史候选) → 下一轮 Solution ID 或结束
```

Local HarnessChange 和 ArtifactChange 是文件 patch；remote HarnessChange 由对应 backend 的研究适配器生成，交给其 Builder 校验与构建。EnvArena 使用声明式 JSON 配置变更并调用 Arena API。ModelChange 是一次训练请求。Workflow 负责执行 Change，VersionManager 只负责创建和读取版本。

## 5. 评测隔离

- Benchmark 只向 Workflow 返回公开 Task，不返回 expected；
- Verifier 是用户提供的 Protocol 实现，可以在实现内部加载隐藏 expected；
- Framework 通过 VerifierSpec 加载实现，并只传入显式的 VerificationRequest；
- Verifier 不直接依赖 TrajectoryStore，TaskResult 中的 TrajectoryRef 由 Workflow 和 Runtime 管理；
- 证据以 EvidenceBundle 引用独立文件，EvidenceStore 只导出显式授权的文件并校验摘要；内容存储不理解特定 Benchmark；
- 具体 Agent 决定导出哪些证据以及读取规则，具体 Workflow 决定收集什么；标准研究流程只给提案阶段 TRAIN 证据；
- Research Agent 可选择只读分页文件工具，禁止 Shell 执行；工具只认识授权路径和内容，不包含 Benchmark 规则；
- VALIDATE 只向 ResearchAgent 暴露 score 和 metrics；
- AgentRuntime.open 必须把 `workspace_root` 作为文件系统隔离边界，不能让 Agent 向上读取其他目录。

## 6. 轨迹来源与存储

```text
Local Runtime / Remote Runtime
            ↓
     TrajectorySource
            ↓ provider payload
   TrajectoryNormalizer
            ↓ canonical Trajectory
      TrajectoryStore
            ↓
       TrajectoryRef
```

TrajectoryStore 的 local/remote 实现共享同一个 Protocol，由 TrajectoryStoreSpec 和 provider factory 选择。远程轨迹可以保留不可变引用，也可以在保存前物化为 snapshot。TrajectoryStore 管理标准轨迹索引，ContentStore 管理轨迹正文和附件，二者不重复承担职责。

## 7. 当前范围

Remote 使用独立数据目录，固定 Benchmark、Harness 基线、Model 和评测策略；RSI Agent 与 ResearchAgent 沿用 `RSI_AGENT_MODELS` 和 `RSI_RUNTIME_CONFIG`。每个 Run 保存模型身份及 Runtime 配置摘要，恢复时同时核对。

Remote 的通用装配与具体后端分开：

```text
modules/remote/
├── base.py          RemoteProvider / RemoteBackend / RemoteContext / RemoteJob
├── registry.py      backend 选择、配置入口和只读状态分发
└── envarena/
    ├── backend.py   组件装配、认证变量声明和 Job 状态投影
    ├── config.py    ArenaConfig
    ├── client.py    Arena API
    ├── research.py  EnvArena 配置格式及轨迹的研究适配
    ├── benchmark.py
    ├── harness.py
    └── runtime.py
```

`RemoteProvider.create` 返回 `RemoteBackend`，组合已有 Benchmark、HarnessRuntime、Verifier、
Evaluation、HarnessBuilder 及 ResearchAgent 工厂，不另造一套运行/评分接口。
通用 `RemoteApp` 管理本地模型和 Runtime，再将这些组件交给 Workflow；Web 通过
provider 的 `read_jobs` 读取本地状态，不连接后端。认证变量同样由 provider 声明。
增加实现时在 `registry.PROVIDERS` 注册，Workflow 和通用 App 无需修改。

`RSI_REMOTE_BACKEND` 选择实现，`RSI_REMOTE_CONFIG` 指定其配置文件。
数据目录及计划绑定 backend，不能跨后端恢复；旧 EnvArena 配置变量和记录保持兼容。
EnvArena 实现包含 `ArenaBenchmark`、`ArenaHarnessRuntime`、`ArenaVerifier`、`ArenaEvaluation`
和 `ArenaHarnessBuilder`。Workflow 通过可选 `BatchHarnessRuntime.run_many` 批量执行，
结果仍按 Task 保存检查点。EnvArena Verifier 读取并核对平台评分，Evaluation 聚合并校验结果。

EnvArena 候选只修改 skill/tool/system prompt/MCP。Runtime、模型、评分器、执行资源和输出契约固定；资源通过 Arena API 创建和绑定，现有发布版本不修改。每次提交先保存意图和唯一 tag；断线后查找并恢复已存在的 Job。没有服务端创建幂等保证时，对无法判定的提交显式报错，不能盲目重发。停止请求传递到远端 Job，恢复会重新连接。

第一阶段固定 Model 执行 Harness evolution，TrainingBackend 暂不实施。Workflow 使用普通 Python 类，不设计 DSL。多 ResearchAgent 从同一基线出发，同一轮并发运行，在轮次边界调用 RSI Agent 剪枝。某条线失败不抹掉其他线的完成记录；恢复只重试未完成部分，剪枝决定持久保存。相同 Run 的跨进程执行由 OS 文件锁互斥。

研究线数量通过装配配置指定；所有 ResearchAgent 接收同一实验目标，不预设不同方向或改进答案。各线拥有独立 Session、workspace、迭代状态和版本分支，选版候选限定为该研究线自己的历史和共同 baseline。新 run 的状态与阶段进度分别保存在 `runs/<run_id>/run.json` 和 `workflow/progress.json`，TUI 与 Web 通过统一 FileRunStore / RunPaths 读取。ResearchIteration 在开始执行时即保存，失败或取消时保留节点与已有检查点引用，恢复复用该节点。

Agent 状态、节点、Memory、轨迹索引、后台会话与 worker 资料集中于各自 run；Memory 不自动读取根目录历史数据。内容和版本可在根目录去重。旧 run 兼容读取，保留其原有检查点及锁位置。详细布局见 [README](../README.md#开发检查与数据)。

数据模型和通用 Service 允许没有起点、没有最佳版本；当前本地装配及 Harness / Artifact 工作流仍需要起点。新 Agent 状态还定义了等待、停止中及会话恢复字段，但每轮报告后等待 RSI 回复、连续会话、Evidence Pool 和动态任务调度属于后续实现。

任务调用携带稳定的 execution_key（运行/评测目录/任务），工作流按检查点复用已完成的任务。已完成任务、提案、候选、选版、轮次均保存检查点；恢复时核对 Benchmark、题面与 Solution，不能拿旧评测缓存测新题。
