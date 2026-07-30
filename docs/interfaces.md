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

Install agent support with:

```bash
pip install -e '.[agent]'
```

For local tests and demos, `FakeRSIAgent` provides deterministic natural
language to action mapping without OpenAI credentials.

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

    def get_trajs(self, job: EvalJob, include_messages: bool = False) -> list[Traj]:
        ...
```

`EnvArenaClient` is the EnvArena-specific implementation. It additionally
exposes registry asset discovery, BenchmarkJob monitoring, and trajectory
detail APIs that are not required by the generic controller contract.

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
