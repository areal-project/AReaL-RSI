# RSI Infra Python API

当前 API 只描述 Workflow 真正需要交换的数据和能力。实现细节放在 Harness 目录、Trajectory 文件或具体模块内部，不再为每个中间步骤建立公共对象。

## 1. 文件结构

`HarnessRuntime.run` 保留单题接口。支持批量的平台可实现可选的
`BatchHarnessRuntime.run_many(solution, tasks, *, config=None) -> Mapping[str, TaskResult]`。
返回值必须精确覆盖输入 Task ID；同一 `execution_key` 的重复调用恢复已提交工作。
批量实现负责取消时的远端停止与恢复，Workflow 继续使用原有逐题结果和评测检查点。

`modules/remote/base.py` 定义后端装配契约：`RemoteProvider.create` 提供基线、上述现有模块
和 ResearchAgent 工厂；`read_jobs` 提供只读的 `RemoteJob` 投影。EnvArena 的 API、配置、
媒体格式和研究适配均位于 `modules/remote/envarena/`，通用 App 不导入这些实现。

```text
src/areal_rsi/api/
├── base.py      基础数据对象
├── modules.py   基础模块接口
└── workflow.py  Agent、Workflow 和用户入口

src/areal_rsi/framework/
├── trajectory.py  TrajectoryStore loader 与本地实现
└── verifier.py    用户 Verifier loader 与统一执行器
```

## 2. 核心数据对象

### 2.1 Task

```python
class Task:
    task_id: str
    input: JsonValue | ContentRef
    metadata: JsonObject
```

Task 是 Benchmark 提供给被评测 Harness 的公开输入。它不包含 expected；用户 Verifier 可以用 task_id 关联自己持有的隐藏答案，Workflow、Harness 和 ResearchAgent 都拿不到 expected。

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

HarnessVersion 的 `content` 指向可复现的配置快照。Local mode 使用完整目录，Prompt、Skill、Tool、MCP 是目录内文件；remote mode 使用远端 Harness 配置及固定资源引用，media type 为 `application/vnd.areal-rsi.arena-harness+json`。运行时由远端提供，本地只调用远端能力配置 API。

ContentRef 是底层文件或目录的内容地址；ArtifactVersion 是用户产物的版本。比如一个游戏目录的位置和摘要由 ContentRef 表示，而 `game_v3` 由 ArtifactVersion 表示：

```python
class ArtifactVersion:
    version_id: str
    name: str
    content: ContentRef
    created_at: str
    parent_version_id: str | None
```

SolutionVersion 记录一次评测实际采用的 target Model、Harness 和用户 Artifact 组合。RSI/Research Agent 的模型可各自配置，其有效模型映射保存在 ExperimentPlan.config，不进入被评测 Solution：

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

Local patch 可以修改多个相互依赖的文件，但整体只产生一个新 HarnessVersion。Remote 的 `patch` 使用 `application/vnd.areal-rsi.arena-change+json`，内容为声明式配置变更，允许字段由远端适配器校验；签名不变。

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
    source: JsonObject
```

执行状态由 Runtime 的正常返回或异常表达；最终输出和附属文件保存在完整轨迹内容中，不在 Trajectory 顶层重复保存。任务是否正确由 VerificationResult 表达。

Verifier 是用户提供的单 Task 验证逻辑。Framework 使用 VerifierSpec 加载实现，并把一次调用需要的公开信息放入 VerificationRequest：

```python
class VerifierSpec:
    verifier_id: str
    provider: str
    entrypoint: str | None
    digest: str | None
    config: JsonObject
    timeout_seconds: float | None

class VerificationRequest:
    task: Task
    solution: SolutionVersion
    result: TaskResult
    verifier: VerifierSpec
    metadata: JsonObject
```

VerificationResult 将 Verifier 执行状态与任务验证结论分开。只有 `status=succeeded` 时，`passed` 和 `score` 才有业务含义；timeout、输入错误和基础设施错误不能自动视为任务零分：

```python
class VerificationResult:
    task_id: str
    trajectory_id: str
    passed: bool | None
    score: float | None
    metrics: dict[str, float]
    feedback: str
    attachments: tuple[ContentRef, ...]
    status: VerificationStatus
    verifier_id: str | None
    verifier_digest: str | None
    error: VerificationError | None
```

使用者只实现 `verify(request)`；Framework 负责加载、超时和错误边界，不提供任何具体任务的判分逻辑：

```python
class ProjectVerifier:
    async def verify(self, request: VerificationRequest) -> VerificationResult:
        passed, score, metrics = await verify_project_output(request.result.output)
        return VerificationResult(
            task_id=request.task.task_id,
            trajectory_id=request.result.trajectory.trajectory_id,
            passed=passed,
            score=score,
            metrics=metrics,
            feedback="",
        )

spec = VerifierSpec(
    verifier_id="project-verifier",
    provider="python",
    entrypoint="project.verifier:ProjectVerifier",
    timeout_seconds=30,
)
verifier = VerifierLoader().create(spec)
verification = await VerifierExecutor().verify(verifier, request)
```

`VerifierExecutor` 会补齐 `verifier_id` 和 `verifier_digest`。用户结论使用 `status=succeeded`；执行超时和框架异常分别返回 `timeout` 和 `infra_error`，此时 `passed`、`score` 必须为 `None`。调用方取消协程时取消信号继续向上传播，不伪装成验证结论。

EvaluationReport 只包含：

```python
solution_id
split
passed
score
metrics
created_at
```

它没有 summary、regressions、case 详情或 expected。TRAIN 细节由 ResearchAgent 从授权的证据文件读取；VALIDATE 只传聚合数值，避免评测泄漏。

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
    evidence,
) -> ResearchChange
```

run_id、research_agent_id 和 iteration 属于 Workflow 运行状态，不需要发送给模型。Memory 由 ResearchAgent 通过工具按需搜索，不预先塞入上下文。`report` 是供宿主检查的 TRAIN EvaluationReport，并不要求发送进模型消息；`evidence` 指向 EvidenceBundle。Agent 实现选择可访问文件并将它们导出到隔离工作目录，再给模型路径和读取规则。

选版也直接从当前研究线的历史候选中进行：

```python
select(
    current_solution_id,
    candidates,
) -> str | None
```

`candidates` 包含 baseline 和该 ResearchAgent 所有已经评测过的 Solution。返回 Solution ID 表示下一轮从该版本继续，返回 None 表示结束研究线。历史最佳由 Workflow 根据 EvaluationReport 计算，不由 ResearchAgent 重复记录。

EvaluationReport 中的 solution_id 可以通过 VersionManager.get_solution 查询完整 Model、Harness、Artifact 和父版本。ResearchAgent 实现需要把 VersionManager 作为只读工具提供给选版 Session，因此无需把完整 Solution 重复放进每份报告。

### ResearchIteration 与工作流结果

ResearchIteration 是通用研究节点，不限定任务类别、产出形式或执行顺序：

```python
class ResearchIteration:
    iteration_id: str
    run_id: str
    agent_id: str
    assignment_ref: RecordRef
    state: IterationState = IterationState.PENDING
    parent_iteration_id: str | None = None
    report_ref: RecordRef | None = None
    references: tuple[RecordRef, ...] = ()
    created_at: str | None = None
    updated_at: str | None = None
```

`RecordRef(uri)` 引用指令、报告或组件记录；本地 RunStore 支持 `run://<run_id>/<path>`、`content://<digest>` 及旧记录兼容引用。引用可带 JSON pointer，解析会检查路径边界；引用本身不代表访问授权。节点状态为 `pending / running / completed / failed / cancelled`。不要求每个节点都有候选或评测，失败节点也保留已有材料。

三种对象的职责不同：

| 对象 | 职责 | 是否为通用节点必需 |
| --- | --- | --- |
| ResearchIteration | 一项研究任务的归属、执行状态、报告和材料引用 | 它本身就是通用节点 |
| EvolutionResult | 现有 Harness / Artifact 迭代的修改、候选、评测及下一版本记录 | 否，由具体工作流选择使用 |
| SolutionVersion | 可执行、可评测的 Model / Harness / Artifacts 版本组合 | 否，当前版本模型由具体执行工具使用 |

当前固定工作流仍使用上面的 propose / select 接口，将完成结果保存为 `workflows.records.EvolutionResult`，由节点 report_ref 引用；`evolution_history` 读取这些结果供选版和恢复使用。EvolutionResult 中的 input_solution_id、candidate_solution_id 和 next_solution_id 指向 SolutionVersion，版本正文仍由 VersionManager 管理。

**可选指工作流可以完全不采用 EvolutionResult，不是要求把它的每个字段都改为 Optional。** 当前 Harness / Artifact 工作流仍需要这种完整记录才能恢复自己的固定流程；通用 ResearchIteration 和 RunStore 不要求报告符合该结构。其他工作流直接引用自身报告或组件记录，不需要生成占位 EvolutionResult，也不应为没有候选或评测的任务补造 SolutionVersion。

`report_ref` 的具体内容格式由工作流决定，不能把所有报告都按 EvolutionResult 解码。完成一项研究任务也不等同于生成可执行版本或通过评测；报告的内容要求由任务与协作协议规定。

ResearchAgentState 使用独立的 `AgentState`：`created / running / waiting / paused / stopping / stopped / failed`，另外记录方向、当前节点、等待原因、会话 ID 和检查点引用。当前工作流已区分运行、停止、失败和取消后的暂停；等待回复、连续会话和动态调度尚待实现，不能由枚举存在推定行为已接入。

`ExperimentPlan.baseline_solution_id` 与 `ExperimentRun.best_solution_id` 可为空。通用 Service 接受无起点计划；当前 Harness / Artifact 执行路径自行要求起点。新 run 的最佳版本初始为空，由工作流在真实评测后更新。

磁盘上 run、Agent 和 ResearchIteration 分别保存在 `runs/<run_id>/run.json`、`agents/<agent_id>/agent.json`、`iterations/<iteration_id>.json`；父记录仅保存子记录 ID。`FileRunStore.get/list` 还原为现有嵌套内存对象，调用方无需重复拼装。Memory、轨迹索引及后台运行数据按 run 装配。

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
query(query) -> TrajectoryPage
bundle(trajectory_refs) -> ContentRef
```

AgentSession.finish 或 HarnessRuntime.run 完成后，Runtime 自动保存 Trajectory。Workflow 不需要向 TrajectoryStore 追加 event。TrajectoryStore 只保存已经完成的标准 Trajectory，不负责执行 Agent，也不解释 provider 原始响应。

`save` 按 trajectory_id 幂等：相同内容重复保存返回同一个 TrajectoryRef；同一 ID 对应不同内容时必须失败，不能覆盖历史轨迹。`TrajectoryRef.store_id=None` 表示使用调用方已经注入的默认 Store；显式 store_id 用于跨 Store 传递引用。

local 与 remote 是配置出的实现，不是两套公共 API：

```python
local = TrajectoryStoreSpec(
    store_id="local",
    provider="filesystem",
    storage_type=TrajectoryStorageType.LOCAL,
    uri="./runs/trajectories",
)

remote = TrajectoryStoreSpec(
    store_id="team-store",
    provider="custom-remote",
    storage_type=TrajectoryStorageType.REMOTE,
    uri="trajectory://team/project",
    materialization=TrajectoryMaterialization.REFERENCE,
    config={"token_env": "TRAJECTORY_STORE_TOKEN"},
)
```

配置不得包含凭证，只能包含保存凭证的环境变量名。`provider` 通过 TrajectoryStoreLoader 的 factory registry 解析。Framework 内置 filesystem provider；远程系统实现自己的 TrajectoryStoreFactory 并注册。

```python
loader = TrajectoryStoreLoader({"custom-remote": ProjectTrajectoryStoreFactory()})
local_store = loader.create(local)
remote_store = loader.create(remote)
```

远端 Runtime 原始响应通过两个独立接口进入 Store：

```text
TrajectorySource.fetch -> provider JsonObject
TrajectoryNormalizer.normalize -> Trajectory
TrajectoryStore.save -> TrajectoryRef
```

`materialization=reference` 保留远端 ContentRef；`materialization=snapshot` 要求对应 provider 将内容复制到目标 Store 或 ContentStore 后再保存 Trajectory。无论哪种模式，调用方读取的都是同一个 TrajectoryStore API。

bundle 将多条轨迹组成标准 manifest，返回 ContentRef，并固定使用 media type：

```text
application/vnd.areal-rsi.trajectories+json
```

manifest 最小格式：

```json
{
  "trajectories": [
    {
      "trajectory_id": "trajectory_1",
      "store_id": "team-store",
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

这些是注册表键的示例，不意味着所有 Runtime 自动实现它们。当前 `PackageAgentRuntime`
按 `RSI_RUNTIME_CONFIG` 选择 Harness，通过公开 Application API 执行，绑定键为
`areal-agent-runtime@v1`。`bash@v1`、`editor@v1` 映射到已配置 Harness 的工具，MCP
使用 `mcp_registry`；未支持或未注册的键会报错。DSH 使用包含 Runtime bundle 的
`agent-runtime` profile。详细能力边界见 [Runtime package](../runtime-package.md)。

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
VerifierLoader.create(spec) -> Verifier
Verifier.verify(request) -> VerificationResult
Evaluation.evaluate(solution, split, results, baseline=None) -> EvaluationReport
```

- Benchmark 只提供公开输入；
- Verifier 是使用者实现的 Protocol，可以内部持有隐藏 expected，但不能自行查询 TrajectoryStore；
- VerificationRequest 显式提供 Task、SolutionVersion、TaskResult 和 VerifierSpec；
- VerifierExecutor 负责 timeout、异常转换、返回类型和 ID 一致性检查；
- Python Verifier 使用 `module:attribute` entrypoint 加载，其他 provider 通过 VerifierFactory 注册；
- Evaluation 聚合一批 VerificationResult，不执行任务、不选择版本。

## 4. Workflow

Workflow 使用普通 Python 类，不设计 DSL。每个 ResearchAgent 执行自己的循环：

```text
current = baseline

for iteration in max_iterations:
    在 TRAIN Tasks 上运行 current
    HarnessRuntime 返回 TaskResult
    分别保存 Task、输出、Trajectory、VerificationResult 和报告，生成 evidence ContentRef
    生成紧凑 train_report

    change = ResearchAgent.propose(goal, target, current, report, evidence)
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

`WorkflowContext.evaluation_concurrency` 控制一次评测内同时执行的任务数，默认是 1；这与 Research Agent 的数量相互独立。生成产物和完整验证结果分别保存检查点，验证器故障恢复时可以复用已生成产物。某个任务出现执行故障时，其他任务仍可完成并保存结果，但本次评测不会把故障计作模型零分后继续汇总。验证器正常完成并拒绝模型输出则属于有效评测结果。

## 5. 证据文件与访问权限

`EvidenceFile(path, content, kind, metadata)` 描述一个文件；`EvidenceBundle(files, metadata)` 保存文件清单。路径、用途和元数据由生产者决定，不包含 Benchmark 特定含义。`EvidenceStore` 提供 `put`、`get`、`materialize`，本地实现为 `FileEvidenceStore`，复用 ContentStore 而不是另建内容仓库。

```python
bundle_ref = await evidence_store.put(EvidenceBundle(files, metadata))
grant_ref = await evidence_store.materialize(
    bundle_ref, workspace / "evidence", paths=explicit_allowed_paths,
)
```

导出必须显式列出授权路径。未知路径、重复路径、目录冲突、路径穿越和软链接目标会被拒绝；内容按摘要校验。返回值记录实际授权文件，导出的 `index.json` 只列出这些文件的路径、大小、用途和摘要，不包含未授权条目或底层存储地址。修改工作副本不会修改原始内容。

当条目的 ContentRef 指向目录快照时，`FileEvidenceStore.put` 会将其展开为独立文件引用。例如 `tasks/1/input` 下的 `nested/question.txt` 会成为 `tasks/1/input/nested/question.txt`，继承原条目的 kind 和 metadata。调用方从 `get` 返回的清单逐文件授权；不会将目录清单中的底层引用直接交给模型，也不会自动授权整个目录。

当前标准 Workflow 收集评测文件，DefaultResearchAgent 的 `evidence_access` 决定导出哪些文件，`evidence_instructions` 决定模型怎么读；自定义 Agent 和 Workflow 可直接使用同一接口。`WorkflowContext.evidence_store` 可替换存储实现。TRAIN 与 VALIDATE 的使用限制是研究方法的策略，不是 EvidenceStore 固定的规则。

标准工作流交给 Research Agent 的目录如下，`output` 保留 TaskResult.output 原始字节，不嵌入轨迹：

```text
workspace/train/
├── index.json
├── report.json
└── tasks/1/
    ├── task.json
    ├── output
    ├── verification.json
    ├── trajectory.json
    └── attachments/1
```

初始提案消息只给目标、可编辑目录、证据目录及读取规则，不自动注入报告、错误或代码。Agent 用工具分段读取；原始文件不截断，工具显示仍有长度上限，超过时必须继续小段读取。实际读取行为保存于模型工具调用轨迹，授权清单保存在工作区外的宿主审计文件。

`DefaultResearchAgent(file_tools_only=True)` 可把提案工具限制为只读文件工具，不提供 Shell 或执行证据代码的工具。通用 `FileReader` 只接收工作目录和显式文件清单，提供 `list_files`、`read_file`、`read_json`、`inspect_json`、`search_file`；它不理解任务、分数或 Benchmark。每次响应最多 6000 字节，文本页最多 4000 个字符，通过 `next_offset` 继续读取；JSON 子树用 JSON Pointer 选择。当前默认仍保留 Shell 工具模式，具体研究方法自行选择。

Package Runtime 不会因启用 MCP 自动开放工作区工具：`bash@v1` 才授权 `workspace.run`，`editor@v1` 只授权文件读写与编辑。依赖原生 Shell 调用 MCP 的 Runtime 无法提供 MCP-only 模式；缺少显式 Bash 授权时会拒绝启动，应选用支持 Application Tool 的 portable Runtime。

文件导出本身不是沙箱。AgentRuntime 还必须禁止访问底层 ContentStore、其他 Agent 工作区和宿主隐藏数据。当前 DSH Runtime 在 macOS 上用 sandbox-exec 执行这个边界；导出副本可写，原始证据不可变。其他 Runtime 需实现等效隔离，不能把提示词当作访问控制。

这是开发期 API 变更：`ResearchAgent.propose` 的第五个参数、`ResearchIteration` 和评测检查点现在使用 `evidence`，不再使用旧的轨迹 bundle。历史证据保留，不静默改写；旧格式实验需新建运行。TrajectoryStore.bundle 仍用于单独的轨迹归档。

## 6. 用户入口

公共 RsiAgent 角色保留以下接口，支持自定义对话入口与多研究线的外层控制：

```python
chat(message) -> str | ExperimentPlan
prune(run) -> ResearchAgent ID[]
```

默认终端入口使用 DSH 原生 TUI，由 DSH 持有对话 Session 和 Agent loop。普通对话直接回复；DSH 经本地 MCP 调用 `rsi_plan(goal, max_iterations)` 生成并展示固定配置的实验计划，此步骤不另外调用聊天模型。

计划的 `config.agent_models` 按 RSI Agent 和 ResearchAgent ID 保存 `{model, version_id}`，所有 `research-agent-N` 必须指向同一个由 `research-agent` 配置解析出的模型。`interaction_model_name` 记录生成计划时的Interaction Agent 模型，`target_model_name` 是 Task Agent 直接执行的 target。恢复时逐个核验研究侧模型，并通过基线 Solution 核验 target；Interaction Agent 和未被使用的 teacher 默认配置不参与绑定。旧计划中重复保存的 `task-agent` 项继续兼容读取。数据目录允许多组模型共存，每个 Run 引用自己的目标基线。

用户确认后，`rsi_start(plan_id, confirmed=true)` 提交独立 worker。worker 核对计划与当前装配，再调用既有实验服务推进 Workflow。退出 TUI 不停止已提交实验；返回 Run ID 只表示提交，结果以真实状态为准。

| MCP 工具 | 行为 |
| --- | --- |
| `rsi_plan` | 生成待确认计划 |
| `rsi_start` | 确认后提交计划，同一计划避免重复启动 |
| `rsi_status` | 返回指定实验或最近实验的有界聚合状态 |
| `rsi_stop` | 请求停止指定后台 worker |
| `rsi_resume` | 确认后恢复指定实验 |

MCP 不提供完整轨迹、隐藏评测明细或凭证。启动和恢复的 `confirmed` 必须是布尔值 `true`；对话 Agent 必须先获得用户对该操作的明确确认。

`rsi tui --resume DSH_SESSION_ID` 恢复对话；`rsi resume RUN_ID` 在当前终端恢复实验。两种标识不能互换。MCP、DSH profile 和 worker 管理属于 `ui/` 的实现，不增加公共角色协议。

### 外部本地应用

本地实验 YAML 可用 `backend` 选择外部应用。开发者在项目根目录执行一次
`rsi local-app register custom your_package.entry:create_app`，将映射保存到
忽略提交的 `.rsi/local-apps.json`。注册会导入可信代码，但不调用工厂或模型。
用户随后在同一项目根目录启动 `rsi tui` 并指定 YAML，无需设置专属环境变量。
注册独立于 `RSI_DATA_ROOT`；换 checkout 需重新注册，更换已有入口需 `--replace`。
部署方仍可用 `RSI_LOCAL_APPS` 提供完整替代映射（包括空映射），优先于注册文件。
YAML 只能选择已注册名称，不能直接指定 Python 代码；注册项属于可信部署配置。

异步工厂接收 `(root: Path, emit: Callable[[str], None])`，返回实现
`ui.experiments.ExperimentApp` 的对象：提供 `baseline`、`rsi`、`service`，以及
`model_plan_config()`、`bind_plan(plan)`、`close()`。规划时不得启动实验或调用任务模型；
后台 worker 重新装配后必须核验计划绑定，关闭时释放自有资源。实现模块须能被
TUI MCP 与 worker 的 Python 环境导入。

注册映射随计划写入启动快照，确认后更改终端环境不会改变该计划的入口。
Python 实现代码并不被此快照冻结；升级实现应创建新计划并自行维护兼容性。
未指定 `backend` 的本地 YAML 继续使用内置应用。

## 7. 当前范围

- 第一阶段实现 Harness evolution；
- TrainingBackend 和 ArtifactBuilder 先保留最小接口，不实现具体训练或产物生成后端；
- 不实现 Workflow DSL、复杂权限和搜索策略；
- Artifact evolution 的首个具体实现以游戏或应用目录为产物。
