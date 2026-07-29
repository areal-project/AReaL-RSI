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
        envarena: EnvArenaClient,
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

### EnvArenaClient

```python
class EnvArenaClient(Protocol):
    def register_model(self, endpoint: Endpoint) -> ModelRef:
        ...

    def update_model(self, model_ref: ModelRef, endpoint: Endpoint) -> ModelRef:
        ...

    def submit_eval(
        self,
        model_ref: ModelRef,
        task_set: str,
        harness_config: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> EvalJob:
        ...

    def submit_rollout(
        self,
        model_ref: ModelRef,
        tasks: list[dict[str, Any]],
        harness_config: dict[str, Any],
        source: TrajSource,
        metadata: dict[str, Any] | None = None,
    ) -> EvalJob:
        ...

    def get_trajs(self, job: EvalJob, include_messages: bool = False) -> list[Traj]:
        ...
```

### ResearchAgent

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

### EvalFirewall

```python
class EvalFirewall(Protocol):
    def summarize(
        self,
        eval_result: dict[str, Any],
        *,
        policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    def decide(
        self,
        baseline: dict[str, Any],
        candidate: dict[str, Any],
        gate: dict[str, Any],
    ) -> dict[str, Any]:
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
