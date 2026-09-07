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

DeepSeek Harness 是 AgentRuntime 的当前实现；已有原创算术与 GSM8K Benchmark，KernelBench 暂缓。核心 API 不出现具体框架或 Benchmark 名称。

三层边界与设计文档一致：User Interface 负责对话和实验控制；Evolver 由 RSI Agent、ResearchAgent、Task 执行和固定 Workflow 组成；Base Module 提供运行、存储、版本、训练、构建与评测能力。AgentSpec 显式列出 Runtime、Model、Prompt Template、Skill、Tool 和 MCP 依赖，避免把组合关系隐藏在不透明配置文件中。

默认对话入口由 DSH 原生 TUI 持有 Session 和 Agent loop，通过本地 MCP 生成计划、查询状态并控制实验。`ExperimentController` 根据用户目标、轮数和本地固定配置生成计划；用户确认后启动独立 worker，worker 装配既有 `ExperimentService` 和 Python Workflow。退出对话不会终止已提交的实验。

前台与后台通过同一个安装模块使用 `RSI_DATA_ROOT` 中的固定版本 DSH CLI。前台加载 TUI profile，后台由 Python SDK 经沙箱包装器加载 `sdk-minimal`；包装器显式指向共享 CLI，共享安装只读，各 Session 的可写工作区独立。

DSH Session ID 标识对话，RSI Run ID 标识实验。后台进程使用文件锁和内核进程启动身份避免重复启动或误停；状态经 MCP 返回有界聚合摘要，详细进度由只读 Web 面板展示。

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

每个 ResearchAgent 从同一 baseline 出发，独立维护 iteration。每轮只修改一个目标，只产生一个候选。并行性来自多个 ResearchAgent，而不是单个 Agent 一次提出多个修改。

RSI Agent 在外层读取 ExperimentRun，返回需要停止的 ResearchAgent ID，对广度搜索做剪枝；ResearchAgent 在各自研究线内部通过 select 做深度搜索和回溯。

## 3. 最小版本模型

```text
ModelVersion + HarnessVersion + ArtifactVersion[] = SolutionVersion
```

- ModelVersion：一个模型快照；
- HarnessVersion：整个 Harness 目录的一个快照，Prompt、Skill、Tool、MCP 都是目录内文件，不再单独建模；
- ArtifactVersion：用户产物的一个快照，例如游戏、应用或生成的数据；
- SolutionVersion：一次实际运行和评测使用的 Model/Harness/Artifact 组合。

版本只记录父版本。版本由哪个 Run、ResearchAgent 和 iteration 产生，可以从 ResearchIteration 的嵌套关系得到，因此不再需要 VersionOrigin。

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
ResearchAgent 通过 ContentRef 读取轨迹，提出一个 Change
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

HarnessChange 和 ArtifactChange 都是一个 patch；ModelChange 是一次训练请求。Workflow 负责执行 Change，VersionManager 只负责创建和读取版本。

## 5. 评测隔离

- Benchmark 只向 Workflow 返回公开 Task，不返回 expected；
- Verifier 是用户提供的 Protocol 实现，可以在实现内部加载隐藏 expected；
- Framework 通过 VerifierSpec 加载实现，并只传入显式的 VerificationRequest；
- Verifier 不直接依赖 TrajectoryStore，TaskResult 中的 TrajectoryRef 由 Workflow 和 Runtime 管理；
- TRAIN 轨迹通过 ContentRef 传递；本地内容由 FileContentStore 校验并复制到隔离工作区，远程内容由对应 Runtime Adapter 解析；
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

第一阶段固定 Model 执行 Harness evolution，TrainingBackend 暂不实施。Workflow 使用普通 Python 类，不设计 DSL。多 ResearchAgent 从同一基线出发，同一轮并发运行，在轮次边界调用 RSI Agent 剪枝。某条线失败不抹掉其他线的完成记录；恢复只重试未完成部分，剪枝决定持久保存。相同 Run 的跨进程执行由 OS 文件锁互斥。

研究线名称、方向通过装配配置指定，提案仍由模型生成，不给每条线预埋改进答案。选版候选限定为该研究线自己的历史和共同 baseline。运行状态与详细阶段进度分别保存在 `run-states/` 和 `runs/<run_id>/progress.json`，TUI 与 Web 读取同一份记录。

任务调用携带稳定的 execution_key（运行/评测目录/任务），工作流按检查点复用已完成的任务。已完成任务、提案、候选、选版、轮次均保存检查点；恢复时核对 Benchmark、题面与 Solution，不能拿旧评测缓存测新题。
