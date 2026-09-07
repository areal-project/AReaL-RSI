# 可选 Artifact 自迭代模块

`ArtifactEvolutionWorkflow` 用于迭代用户产物，例如应用源码、游戏目录或生成文档。它是显式注册的 Python 工作流；默认服务和默认 Harness 入口不会自动开启它，也不会把 Harness 优化改成产物优化。

每条研究线从基线 Solution 开始，每轮执行 TRAIN、提出一个 `ArtifactChange`、构建候选、执行 VALIDATE，再由 ResearchAgent 选版。多研究线并发、轮次边界剪枝、任务检查点、停止和恢复与 Harness 工作流共用。每个候选固定 Model 和 Harness，只创建或更新 `artifact_name` 指定的产物，保留其他产物的版本及顺序；产物版本和 Solution 都记录各自的父版本。空 `SolutionVersion.artifacts` 也能作为创建第一个产物的基线。

## 显式装配和注册

用户先准备 `WorkflowContext` 中的 Benchmark、HarnessRuntime、Verifier、Evaluation、ResearchAgent 等组件，并设置 `artifact_builder`。本地内容仓库可使用 `LocalArtifactBuilder(content_store, root / "artifact-builds")`；构建器接受目录快照和统一 diff，不执行产物。

下面的函数接收已装配的上下文和已注册的基线 Solution：

```python
from pathlib import Path

from areal_rsi.api.base import EvolutionTarget, ExperimentPlan, ExperimentRun
from areal_rsi.api.workflow import WorkflowContext
from areal_rsi.workflows.artifact import ArtifactEvolutionWorkflow
from areal_rsi.workflows.evolution import HarnessEvolutionWorkflow
from areal_rsi.workflows.service import LocalExperimentService


async def run_artifact_experiment(
    root: Path, context: WorkflowContext, baseline_solution_id: str
) -> ExperimentRun:
    service = LocalExperimentService(
        root,
        context,
        print,
        workflows={
            "harness-evolution": HarnessEvolutionWorkflow,
            "artifact-evolution": ArtifactEvolutionWorkflow,
        },
    )
    plan = ExperimentPlan(
        goal="根据真实任务反馈改进用户产物",
        workflow="artifact-evolution",
        benchmark=context.benchmark.name,
        baseline_solution_id=baseline_solution_id,
        target=EvolutionTarget.ARTIFACT,
        max_iterations=3,
        research_agent_count=len(context.research_agents),
    )
    run = await service.start(plan)
    return await service.tasks[run.run_id]
```

传入 `workflows` 会替换服务注册表；示例同时注册两种工作流。只运行产物实验时可以仅注册 `artifact-evolution`。注册不会自动切换上下文，运行不同目标时仍需装配对应的 ResearchAgent 和 Builder。ResearchAgent 的 `propose` 必须处理 `EvolutionTarget.ARTIFACT` 并返回 `ArtifactChange(artifact_name, patch, reason)`，`select` 只能选择该研究线已验证的候选或公共基线，返回 `None` 则结束该线。

配套的可选实现是 `areal_rsi.agents.artifact.ArtifactResearchAgent`，构造时显式传入 `artifact_name`；它读取目标产物和 TRAIN 证据，提出文本文件编辑。用户也可以直接注入符合 `ResearchAgent` 协议的自定义实现。

若还需要自然语言计划入口，可以显式构造 `DefaultRsiAgent(..., workflow_name="artifact-evolution", evolution_target=EvolutionTarget.ARTIFACT)`。它与上面的 Artifact 上下文配套使用；现有 `LocalApp`/TUI 的默认配置仍为 Harness，不会仅凭注册名称自动换用 Artifact 评测组件。

## 评测与恢复

- Benchmark 负责提供 TRAIN / VALIDATE 的公开任务；HarnessRuntime 接收包含候选产物的完整 Solution，负责在用户定义的环境内运行或检查它。
- Verifier 负责每题的确定性结果，Evaluation 负责聚合；本模块不内置游戏评分、浏览器或部署方式。私有验收数据应留在用户的 Verifier 内。
- 使用同一目录及相同组件注册表重新创建服务后，可调用 `await service.resume(run_id)`，再等待 `service.tasks[run_id]`。已落盘的任务、提案、候选和选择结果会复用。Benchmark 身份和题目、ResearchAgent ID 应保持一致。
- 任务调用携带稳定的 `execution_key`。进程在任务执行完成、检查点落盘之前退出时，恢复可能重新执行该任务。版本写入与候选检查点也不是跨存储事务，极窄崩溃窗口可能留下未被候选引用的不可变版本。

运行离线契约测试：

```bash
uv run --locked pytest tests/test_artifact_workflow.py tests/test_workflows.py
```

测试使用明确标记的替身组件检查双线版本链、首个产物创建、其他产物保留、剪枝、失败恢复、逐任务缓存和固定 Model/Harness 的约束；不调用模型、不启动外部服务，分数不构成真实产物质量或性能证据。
