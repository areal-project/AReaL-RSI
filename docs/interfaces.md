# AReaL-RSI Interface Contracts

This document mirrors the current Python interface skeleton under `areal_rsi/`.

## 1. Core Payloads

Payloads remain dictionary-shaped in the MVP so real AReaL and EnvArena adapters can evolve without forcing early rigid schemas.

```python
TrainMethod = Literal["sft", "opd"]
TrajSource = Literal["eval", "research_validation", "opd"]

Endpoint = dict[str, Any]
ModelRef = dict[str, Any]
EvalJob = dict[str, Any]
Traj = dict[str, Any]
Dataset = dict[str, Any]
TrainRun = dict[str, Any]
MemoryContext = dict[str, Any]
```

## 2. RSIAgent

`RSIAgent` is the user-facing coordinator.

The concrete scaffold is `OpenAIRSIAgent`, backed by the optional
`openai` dependency. The SDK is imported lazily so controller and
mock-service tests do not require OpenAI credentials.
For user-facing commands, `OpenAIRSIAgent` uses OpenAI Responses API
function tools and converts returned tool calls into local `Action`
objects. It does not parse free-form JSON text for RSI control actions.

Install agent support with:

```bash
pip install -e '.[agent]'
```

For local tests and demos, `FakeRSIAgent` provides deterministic natural
language to action mapping without OpenAI credentials.

User-facing actions are goal/query/control commands only:

```python
ActionType = Literal[
    "start_loop",
    "get_status",
    "inspect_run",
    "resume_run",
    "stop_run",
]
```

Workflow steps such as `eval`, `generate_dataset`, `train`, and
`holdout_eval` are internal controller steps and appear in audit logs, not
in RSIAgent user actions.

The preferred `start_loop` path loads a base RSI config first. RSIAgent then
emits only dot-path config overrides inferred from the user message:

```json
{
  "type": "start_loop",
  "args": {
    "overrides": {
      "run.initial_ckpt": "ckpt_123",
      "env.task_set": "swe_verified",
      "run.max_iterations": 2
    }
  }
}
```

The controller merges these overrides into the loaded config, writes
`resolved_rsi_config.json` under the run directory, records
`run_config_resolved` in audit, and executes the resolved config.

```python
class RSIAgent(Protocol):
    def chat(
        self,
        message: str,
        *,
        run_id: str | None = None,
        controller: object,
        memory: object,
        workflow_spec: dict[str, Any],
    ) -> AgentResponse:
        ...

    def propose_next_action(
        self,
        *,
        run_state: RunState,
        memory_context: MemoryContext,
        workflow_spec: dict[str, Any],
    ) -> Action:
        ...

    def explain(
        self,
        *,
        run_id: str,
        controller: object,
        memory: object,
        question: str,
    ) -> str:
        ...
```

## 3. RSIController

`RSIController` is the deterministic workflow executor.

```python
class RSIController(Protocol):
    def execute_action(self, action: Action) -> dict[str, Any]:
        ...

    def run_iteration(
        self,
        ckpt: str,
        *,
        areal: AReaLService,
        envarena: EnvClient,
        research_agent: ResearchAgent,
        memory: Memory,
        task_set: str,
        harness_config: dict[str, Any],
        method: TrainMethod = "sft",
        train_config: dict[str, Any] | None = None,
        traj_filter: dict[str, Any] | None = None,
        eval_gate: dict[str, Any] | None = None,
    ) -> RunState:
        ...

    def run_loop(
        self,
        initial_ckpt: str,
        *,
        task_set: str,
        harness_config: dict[str, Any],
        method: TrainMethod = "sft",
        max_iterations: int = 3,
        stop_criteria: dict[str, Any] | None = None,
        train_config: dict[str, Any] | None = None,
        traj_filter: dict[str, Any] | None = None,
        eval_gate: dict[str, Any] | None = None,
    ) -> RunState:
        ...

    def get_status(self, run_id: str) -> RunState:
        ...

    def resume(self, run_id: str) -> RunState:
        ...

    def stop(self, run_id: str, reason: str | None = None) -> RunState:
        ...
```

## 4. Service Interfaces

### AReaLService

```python
class AReaLService(Protocol):
    def ensure_rollout_endpoint(self, ckpt: str, config: dict[str, Any] | None = None) -> Endpoint:
        ...

    def update_rollout_endpoint(self, endpoint: Endpoint, ckpt: str) -> Endpoint:
        ...

    def train(
        self,
        base_ckpt: str,
        dataset: Dataset,
        method: TrainMethod,
        config: dict[str, Any],
        *,
        envarena: object | None = None,
        model_ref: ModelRef | None = None,
        harness_config: dict[str, Any] | None = None,
    ) -> TrainRun:
        ...
```

### EnvClient

```python
class EnvClient(Protocol):
    def register_model(self, endpoint: Endpoint) -> ModelRef:
        ...

    def update_model(self, model_ref: ModelRef, endpoint: Endpoint) -> ModelRef:
        ...

    def submit_eval(self, request: EvalRequest) -> EvalJob:
        ...

    def get_eval_progress(self, job: EvalJob) -> dict:
        ...

    def get_eval_result(self, job: EvalJob) -> dict:
        ...

    def get_trajs(self, job: EvalJob, include_messages: bool = False) -> list[Traj]:
        ...
```

`EnvClient` and `MockEnvClient` live in `areal_rsi.services.env_client`.
`EnvArenaClient`, its configuration, and its generated SDK live under
`areal_rsi.services.env_client.envarena`; it additionally
exposes registry asset discovery, native BenchmarkJob summary/detail APIs, and
trajectory-detail APIs that are not required by the generic controller contract.

### ResearchAgent

The concrete scaffold is `OpenAIResearchAgent`, also backed by
`openai`. The mock implementation remains available for local M1
controller tests.

```python
class ResearchAgent(Protocol):
    def generate_dataset(
        self,
        trajs: list[Traj],
        memory_context: MemoryContext,
        current_ckpt: str,
        *,
        envarena: object | None = None,
        model_ref: ModelRef | None = None,
        harness_config: dict[str, Any] | None = None,
        traj_filter: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
    ) -> Dataset:
        ...

    def choose_ckpt(self, train_runs: list[TrainRun], memory_context: MemoryContext) -> str:
        ...
```

## 5. Memory

`AuditLogger` records machine-readable action facts for one run. It is owned by
`RSIController`, not by service adapters or agents. The file location is
`runs/<run_id>/audit.jsonl`.

Each action writes a `started` event and either a `succeeded` or `failed`
event. Large payloads stay in artifacts; audit rows store summaries and
artifact paths.

```python
class AuditLogger:
    def action(
        self,
        *,
        run_id: str,
        iteration: int,
        step: str,
        event_type: str = "workflow_step",
        input: dict[str, Any] | None = None,
        actor: str = "RSIController",
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[AuditAction]:
        ...

    def record_event(
        self,
        *,
        run_id: str,
        event_type: str,
        status: str = "succeeded",
        iteration: int | None = None,
        step: str | None = None,
        input: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
    ) -> None:
        ...
```

Audit event shape:

```json
{
  "schema_version": "audit.v1",
  "run_id": "rsi_run_001",
  "iteration": 1,
  "action_id": "act_001_...",
  "event_type": "workflow_step",
  "step": "eval",
  "status": "succeeded",
  "input": {},
  "output": {
    "score": {
      "mean_reward": 0.8
    }
  },
  "artifacts": {
    "eval_job": "artifacts/iter_001/eval_job.json"
  },
  "error": null
}
```

Non-step events use the same envelope with distinct `event_type`, for
example `user_goal_received` before the loop starts and `loop_decision`
after each holdout evaluation.

`Memory` records higher-level summaries, research observations, decisions,
and human feedback. It is the context source for `RSIAgent` and
`ResearchAgent`.

```python
class Memory:
    def record(self, title: str, content: str, metadata: dict[str, Any] | None = None) -> str:
        ...

    def get_context(
        self,
        ckpt: str | None = None,
        run_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        ...

    def append_ledger(self, event: dict[str, Any]) -> None:
        ...
```
