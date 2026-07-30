"""RSIController contract."""

from __future__ import annotations

from typing import Any, Protocol

from areal_rsi.controller.run_state import RunState
from areal_rsi.schemas.actions import Action
from areal_rsi.schemas.core import TrainMethod
from areal_rsi.services.areal import AReaLService
from areal_rsi.services.env_client import EnvClient
from areal_rsi.services.research import ResearchAgent
from areal_rsi.memory.memory import Memory


class RSIController(Protocol):
    """Deterministic workflow executor for structured RSI actions."""

    def execute_action(self, action: Action) -> dict[str, Any]:
        """Execute a structured action emitted by RSIAgent."""

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
        """Run one deterministic RSI iteration."""

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
        """Run multiple RSI iterations under one run id."""

    def get_status(self, run_id: str) -> RunState:
        """Return current run state."""

    def resume(self, run_id: str) -> RunState:
        """Resume an interrupted workflow."""

    def stop(self, run_id: str, reason: str | None = None) -> RunState:
        """Stop a workflow and record the reason."""
