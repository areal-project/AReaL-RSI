# Python RSI Infra 架构边界

## 1. 组件与数据流

```text
┌──────────────────── User Interface ────────────────────┐
│ TUI / CLI ⇄ RSI Agent ⇄ ExperimentService             │
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

DeepSeek Harness 是 AgentRuntime 的第一种实现；KernelBench 是 Benchmark 的第一种实现。核心 API 不出现具体框架或 Benchmark 名称。

三层边界与设计文档一致：User Interface 负责对话和实验控制；Evolver 由 RSI Agent、ResearchAgent、Task 执行和固定 Workflow 组成；Base Module 提供运行、存储、版本、训练、构建与评测能力。AgentSpec 显式列出 Runtime、Model、Prompt Template、Skill、Tool 和 MCP 依赖，避免把组合关系隐藏在不透明配置文件中。

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
    ↓ Verifier
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
- Verifier 根据 task_id 在实现内部加载隐藏 expected；
- TRAIN 轨迹通过 ContentRef 传递；本地或远程数据由 ContentStore/Runtime Adapter 解析；
- VALIDATE 只向 ResearchAgent 暴露 score 和 metrics；
- AgentRuntime.open 必须把 `workspace_root` 作为文件系统隔离边界，不能让 Agent 向上读取其他目录。

## 6. 当前范围

第一阶段执行 Harness evolution。TrainingBackend 和 ArtifactBuilder 先定义最小抽象；Model evolution 与 Artifact evolution 的具体实现后续接入。Workflow 使用普通 Python 类，不设计 DSL。
