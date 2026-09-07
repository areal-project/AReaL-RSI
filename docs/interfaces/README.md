# RSI Infra Python API

当前 API 只描述 Workflow 真正需要交换的数据和能力。实现细节放在 Harness 目录、Trajectory 文件或具体模块内部，不再为每个中间步骤建立公共对象。

## 1. 文件结构

```text
src/areal_rsi/api/
├── base.py      基础数据对象
├── modules.py   基础模块接口
└── workflow.py  Agent、Workflow 和用户入口
```

## 2. 核心数据对象

### 2.1 Task

```python
class Task:
    task_id: str
    input: JsonValue | ContentRef
    metadata: JsonObject
```

Task 是 Benchmark 提供给被评测 Harness 的公开输入。它不包含 expected；Verifier 根据 task_id 在内部加载隐藏答案，Workflow、Harness 和 ResearchAgent 都拿不到 expected。

KernelBench 是 Benchmark 的一种实现，不是公共接口。

### 2.2 ModelVersion、HarnessVersion、ArtifactVersion、SolutionVersion

```text
ModelVersion + HarnessVersion + ArtifactVersion[] = SolutionVersion
```

ModelVersion 和 HarnessVersion 的结构一致：

```python
class HarnessVersion:
    version_id: str
    name: str
    content: ContentRef
    created_at: str
    parent_version_id: str | None
```

HarnessVersion 的 `content` 指向完整 Harness 目录快照。Prompt、Skill、Tool、MCP 都是这个目录中的文件，不再定义 HarnessResource 或 HarnessManifest。

ContentRef 是底层文件或目录的内容地址；ArtifactVersion 是用户产物的版本。比如一个游戏目录的位置和摘要由 ContentRef 表示，而 `game_v3` 由 ArtifactVersion 表示：

```python
class ArtifactVersion:
    version_id: str
    name: str
    content: ContentRef
    created_at: str
    parent_version_id: str | None
```

SolutionVersion 记录一次运行实际采用的 Model、Harness 和用户 Artifact 组合：

```python
class SolutionVersion:
    solution_id: str
    model: ModelVersion
    harness: HarnessVersion
    created_at: str
    artifacts: tuple[ArtifactVersion, ...]
    parent_solution_id: str | None
```

版本谱系由 parent 字段表达。版本来自哪个实验和研究轮次，可以从外层 ResearchIteration 找到，不再重复保存 VersionOrigin。

### 2.3 HarnessChange、ModelChange 与 ArtifactChange

每个 ResearchAgent 每个 iteration 只提出一个修改：

```python
HarnessChange | ModelChange | ArtifactChange
```

HarnessChange 是一个针对当前 Harness 快照的 patch：

```python
class HarnessChange:
    patch: ContentRef
    reason: str
```

patch 可以修改多个相互依赖的文件，但整体只产生一个新 HarnessVersion，因此不再拆成 HarnessEdit 和 HarnessChange 两层。

ModelChange 是一次训练请求：

```python
class ModelChange:
    training_data: ContentRef
    config: JsonObject
    reason: str
```

Workflow 根据目标调用 HarnessBuilder、TrainingBackend 或 ArtifactBuilder 执行修改，再让 VersionManager 创建新版本。

ArtifactChange 修改一个用户产物：

```python
class ArtifactChange:
    artifact_name: str
    patch: ContentRef
    reason: str
```

ArtifactBuilder 可以用 patch 更新已有产物，也可以在没有父版本时创建新产物。以游戏设计为例，ArtifactVersion 保存每一版游戏目录，Evaluation 根据可执行结果或用户 Verifier 对不同版本评分。

### 2.4 TaskResult、Trajectory、VerificationResult、EvaluationReport

```text
TaskResult（主输出 + TrajectoryRef）
    ↓ Verifier
VerificationResult（一个 Task）
    ↓ Evaluation
EvaluationReport（一批 Task 的聚合指标）
```

HarnessRuntime 返回 TaskResult，让 Verifier 明确知道主要产物和对应轨迹：

```python
class TaskResult:
    output: ContentRef
    trajectory: TrajectoryRef
```

例如 KernelBench 的 output 指向生成的 Kernel；游戏评测的 output 可以指向截图或测试结果目录。Trajectory 本身只保存轨迹索引和完整轨迹文件的 ContentRef：

```python
class Trajectory:
    trajectory_id: str
    agent_id: str
    content: ContentRef
    usage: JsonObject
    created_at: str
    task_id: str | None
    solution_id: str | None
```

执行状态由 Runtime 的正常返回或异常表达；最终输出和附属文件保存在完整轨迹内容中，不在 Trajectory 顶层重复保存。任务是否正确由 VerificationResult 表达。

EvaluationReport 只包含：

```python
solution_id
split
passed
score
metrics
created_at
```

它没有 summary、regressions、case 详情或 expected。TRAIN 细节由 ResearchAgent 从轨迹目录读取；VALIDATE 只传聚合数值，避免评测泄漏。

### 2.5 ResearchAgent 与 iteration

多个 ResearchAgent 是多条独立研究线：

```text
Run
├── Agent 1：iteration 1 → 2 → 3
└── Agent 2：iteration 1 → 2 → 3
```

ResearchAgent 的方法直接接收真正用于研究的数据，不再定义 ResearchInput：

```python
propose(
    goal,
    target,
    current,
    report,
    trajectories,
) -> ResearchChange
```

run_id、research_agent_id 和 iteration 属于 Workflow 运行状态，不需要发送给模型。Memory 由 ResearchAgent 通过工具按需搜索，不预先塞入上下文。`report` 必须是 TRAIN split 的 EvaluationReport；`trajectories` 是指向本地轨迹 manifest 的 ContentRef，不暴露本机路径。FileContentStore 校验摘要，Runtime 将内容复制到隔离工作区。

选版也直接从当前研究线的历史候选中进行：

```python
select(
    current_solution_id,
    candidates,
) -> str | None
```

`candidates` 包含 baseline 和该 ResearchAgent 所有已经评测过的 Solution。返回 Solution ID 表示下一轮从该版本继续，返回 None 表示结束研究线。历史最佳由 Workflow 根据 EvaluationReport 计算，不由 ResearchAgent 重复记录。

EvaluationReport 中的 solution_id 可以通过 VersionManager.get_solution 查询完整 Model、Harness、Artifact 和父版本。ResearchAgent 实现需要把 VersionManager 作为只读工具提供给选版 Session，因此无需把完整 Solution 重复放进每份报告。

ResearchIteration 保存一轮完整记录：

```python
class ResearchIteration:
    iteration: int
    input_solution_id: str
    train_report: EvaluationReport
    trajectories: ContentRef
    change: HarnessChange | ModelChange | ArtifactChange
    candidate_solution_id: str
    validation_report: EvaluationReport
    next_solution_id: str | None
    started_at: str
    finished_at: str
```

ResearchIteration 只记录已经成功完成的一轮，不保存构建中、评测中等半成品状态。`next_solution_id=None` 唯一表示本轮完成后结束研究线；中途失败写入 ResearchAgentState.error 和 Trajectory。ResearchAgentState 只保存 Agent 状态和 iteration 列表，current 可以从最后一次 next_solution_id 推导，best 可以从历史 validation_report 计算。

## 3. 基础模块接口

### ContentStore

```python
put_bytes / put_text / put_json
read_bytes / read_text / read_json
resolve_path
```

保存普通文件或目录内容并返回带 digest 的 ContentRef。它不管理用户 Artifact 的版本语义。

### Memory

```python
add(text, source, tags)
get(memory_id)
search(query, limit)
```

Memory 保存跨 iteration 或跨 run 仍有价值的结论。原始日志留在 Trajectory 中，不复制进 Memory。

### TrajectoryStore

```python
save(trajectory)
get(trajectory_id)
bundle(trajectory_refs) -> ContentRef
```

AgentSession.finish 或 HarnessRuntime.run 完成后，Runtime 自动保存 Trajectory。Workflow 不需要向 TrajectoryStore 追加 event。bundle 将多条轨迹组成标准 manifest，返回 ContentRef，并固定使用 media type：

```text
application/vnd.areal-rsi.trajectories+json
```

manifest 最小格式：

```json
{
  "trajectories": [
    {
      "trajectory_id": "trajectory_1",
      "content": {
        "uri": "content://0000000000000000000000000000000000000000000000000000000000000000",
        "digest": "sha256:..."
      }
    }
  ]
}
```

### VersionManager

```python
create_model(name, content, parent_version_id=None)
create_harness(name, content, parent_version_id=None)
create_artifact(name, content, parent_version_id=None)
create_solution(model, harness, artifacts=(), parent_solution_id=None)
get_model / get_harness / get_artifact / get_solution
list_child_solutions(solution_id)
```

VersionManager 使用 append-only 方式创建版本和保存父子关系，不覆盖历史版本。`create_solution` 直接接收 ModelVersion 和 HarnessVersion，不要求调用方在 ID 之间来回查询。current/best 属于 Run 的研究状态，不在 VersionManager 中重复保存。

### AgentRuntime

```python
class AgentSpec:
    agent_id: str
    runtime: str
    model: ModelVersion
    prompt_templates: tuple[ContentRef, ...]
    skills: tuple[ContentRef, ...]
    tools: tuple[str, ...]
    mcp_servers: tuple[str, ...]

class AgentSession:
    send(message) -> str
    finish() -> TrajectoryRef

AgentRuntime.open(agent, workspace_root=None) -> AgentSession
```

AgentSpec 显式描述 Agent 的依赖：runtime 使用 `name@version` 形式选择 Runtime Framework，例如 `deepseek-harness@v1`；model 是实际使用的 ModelVersion；Prompt Template 和 Skill 使用带 digest 的 ContentRef；Tool 和 MCP Server 也使用注册表中的 `name@version` 标识，例如 `memory@v1`、`trajectory-store@v1`、`browser@v1`。不再提供一个可隐藏任意字段的通用 config。

这些是注册表键的示例，不意味着所有 Runtime 自动实现它们。当前 DSH Adapter 内置 `bash@v1`、`editor@v1`；其他工具通过构造参数 `tool_registry` 显式绑定到 DSH 插件，MCP 使用 `mcp_registry`。未注册的键会报错，不会静默忽略。ResearchAgent 当前通过工作区内的有界检索脚本读取 Memory，而不是假装存在未装配的 Memory 插件。

AgentRuntime 使用 Session 表达多轮 Agent。每次 `send()` 只返回当前轮回复；`finish()` 结束 Session，Runtime 自动保存完整多轮轨迹并返回 TrajectoryRef。Runtime 必须将 `workspace_root` 作为隔离根目录，不能让文件/Shell 工具向上读取 VALIDATE、expected 或其他 Agent 的目录。

RSI Agent Adapter 和 ResearchAgent Adapter 负责把字符串回复解析成 ExperimentPlan、HarnessChange 等业务对象；通用 Runtime 不返回重复的 text/data 字段。

### HarnessRuntime

```python
run(solution, task, config=None) -> TaskResult
```

用于运行用户正在被进化的 Harness。当前 LocalHarnessRuntime 将输入和版本快照复制到隔离工作区，通过 DSH 执行任务并保存产物与轨迹。

### HarnessBuilder、TrainingBackend 与 ArtifactBuilder

```python
HarnessBuilder.build(base_harness, harness_change) -> ContentRef
TrainingBackend.train(base_model, model_change) -> ContentRef
ArtifactBuilder.build(base_artifact, artifact_change) -> ContentRef
```

三个模块都只生成候选内容，VersionManager 负责创建正式版本。TrainingBackend 是后端中立接口，AReaL 只能是实现之一。

目标 Solution 的推理和任务执行已经由 HarnessRuntime 抽象，Infra Agent 的推理由 AgentRuntime 抽象，因此不再额外定义一套重复的 InferenceBackend。

### Benchmark、Verifier、Evaluation

```python
Benchmark.list_tasks(split) -> Task[]
Verifier.verify(task, solution, result) -> VerificationResult
Evaluation.evaluate(solution, split, results, baseline=None) -> EvaluationReport
```

- Benchmark 只提供公开输入；
- Verifier 内部持有 expected，并通过 SolutionVersion 和 TaskResult.output 获取需要验证的用户产物或单次任务输出；
- Evaluation 聚合一批 VerificationResult，不执行任务、不选择版本。

## 4. Workflow

Workflow 使用普通 Python 类，不设计 DSL。每个 ResearchAgent 执行自己的循环：

```text
current = baseline

for iteration in max_iterations:
    在 TRAIN Tasks 上运行 current
    HarnessRuntime 返回 TaskResult
    保存 Trajectory 和 VerificationResult，生成 trajectories ContentRef
    生成紧凑 train_report

    change = ResearchAgent.propose(goal, target, current, report, trajectories)
    根据 target 调用 HarnessBuilder、TrainingBackend 或 ArtifactBuilder
    创建 candidate SolutionVersion
    在 VALIDATE Tasks 上评测 candidate

    next = ResearchAgent.select(current.solution_id, 所有历史候选报告)
    记录 ResearchIteration
    如果 next 为 None：结束当前研究线
    否则：current = VersionManager.get_solution(next)
```

多个 ResearchAgent 可以异步运行，不要求处于相同 iteration。Run 的全局 best 由 Workflow 使用统一 VALIDATE 指标从所有研究线中更新。

Workflow 在研究轮次之间把最新 ExperimentRun 交给 `RsiAgent.prune(run)`。它返回需要停止的 ResearchAgent ID，实现设计文档中的外层 BFS 剪枝；每个 ResearchAgent 自己的 `select` 负责研究线内部的 DFS 继续或回溯。

RsiWorkflow.run 直接返回 ExperimentRun，不再用 WorkflowResult 再包装一层。WorkflowContext 中三种候选生成后端都是可选项；Workflow 根据 `ExperimentPlan.target` 检查并使用对应实现：HARNESS 需要 HarnessBuilder，MODEL 需要 TrainingBackend，ARTIFACT 需要 ArtifactBuilder。

## 5. 轨迹目录与上下文

ResearchAgent 的初始上下文只包含紧凑 report、当前 Solution 和 trajectories ContentRef。完整细节保存在本地内容仓库中：

```text
trajectories ContentRef
└── materialized workspace/
    ├── report.json
    ├── results.jsonl
    └── trajectories/
        ├── task_001.json
        └── task_002.json
```

本地 Runtime 校验 ContentRef 并将内容复制到隔离 workspace。Agent 使用 `rg`、`head`、`sed`、`jq` 等工具按需分析。工具输出必须截断；每个 iteration 推荐使用新的有界 Session，关键结论写入 Memory，不能持续累积全部历史对话。

## 6. 用户入口

RsiAgent 同时是 TUI 的自然语言入口和多 ResearchAgent 的外层控制者：

```python
chat(message) -> str | ExperimentPlan
prune(run) -> ResearchAgent ID[]
```

RsiAgent 实例内部持有多轮 AgentSession，因此每次 chat 不需要重复传 session_id。普通对话直接返回字符串；识别到 RSI 意图时返回 ExperimentPlan。Run 状态由 ExperimentService 单独管理，不混进聊天输入。用户确认后调用：

```python
ExperimentService.start(plan)
```

TUI 为一段多轮对话持有同一个 AgentSession，每次用户消息调用一次 `send()`；对话结束时调用 `finish()` 保存整段轨迹。

随后通过 `status/stop/resume/list` 管理 Run。

## 7. 当前范围

- 第一阶段实现 Harness evolution；
- TrainingBackend 和 ArtifactBuilder 先保留最小接口，不实现具体训练或产物生成后端；
- 不实现 Workflow DSL、复杂权限和搜索策略；
- Artifact evolution 的首个具体实现以游戏或应用目录为产物。
