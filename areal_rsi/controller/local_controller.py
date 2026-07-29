"""Local concrete RSIController implementation."""

from __future__ import annotations

import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from areal_rsi.controller.run_state import RunState
from areal_rsi.memory.artifacts import ArtifactStore
from areal_rsi.memory.memory import Memory
from areal_rsi.schemas.actions import Action
from areal_rsi.schemas.core import TrainMethod
from areal_rsi.services.areal_service import AReaLService
from areal_rsi.services.envarena_client import EnvArenaClient
from areal_rsi.services.research_agent import ResearchAgent


class LocalRSIController:
    """File-backed controller for local runs and mock integration tests."""

    def __init__(
        self,
        run_root: str | Path = "runs",
        *,
        areal: AReaLService | None = None,
        envarena: EnvArenaClient | None = None,
        research_agent: ResearchAgent | None = None,
        memory: Memory | None = None,
    ):
        self.run_root = Path(run_root)
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.areal = areal
        self.envarena = envarena
        self.research_agent = research_agent
        self.memory = memory

    def execute_action(self, action: Action) -> dict[str, Any]:
        if action.type == "run_iteration":
            state = self.run_iteration(**action.args)
            return state.to_dict()
        if action.type == "get_status":
            return self.get_status(str(action.args["run_id"])).to_dict()
        if action.type == "resume":
            return self.resume(str(action.args["run_id"])).to_dict()
        if action.type == "stop":
            return self.stop(
                str(action.args["run_id"]),
                reason=action.args.get("reason"),
            ).to_dict()
        raise ValueError(f"Unsupported action type: {action.type}")

    def run_iteration(
        self,
        ckpt: str,
        *,
        task_set: str,
        harness_config: dict[str, Any],
        method: TrainMethod = "sft",
        train_config: dict[str, Any] | None = None,
        traj_filter: dict[str, Any] | None = None,
        eval_gate: dict[str, Any] | None = None,
        areal: AReaLService | None = None,
        envarena: EnvArenaClient | None = None,
        research_agent: ResearchAgent | None = None,
        memory: Memory | None = None,
    ) -> RunState:
        areal = areal or self._require(self.areal, "AReaLService")
        envarena = envarena or self._require(self.envarena, "EnvArenaClient")
        research_agent = research_agent or self._require(self.research_agent, "ResearchAgent")
        memory = memory or self._require(self.memory, "Memory")

        run_id = self._new_run_id()
        run_dir = self.run_root / run_id
        artifacts = ArtifactStore(run_dir / "artifacts")
        state = RunState(
            run_id=run_id,
            ckpt=ckpt,
            status="running",
            metadata={
                "task_set": task_set,
                "method": method,
                "harness_config": harness_config,
            },
        )
        self._save_state(state)
        self._record(memory, "iteration_started", f"ckpt={ckpt}, task_set={task_set}, method={method}", state)

        try:
            endpoint = areal.ensure_rollout_endpoint(ckpt, config={"backend": "mock-or-adapter"})
            state.mark_step("ensure_rollout_endpoint", endpoint=endpoint)
            self._save_state(state)
            artifacts.write_json("endpoint.json", endpoint)

            model_ref = envarena.register_model(endpoint)
            state.mark_step("register_model", model_ref=model_ref)
            self._save_state(state)
            artifacts.write_json("model_ref.json", model_ref)

            eval_job = envarena.submit_eval(
                model_ref=model_ref,
                task_set=task_set,
                harness_config=harness_config,
                metadata={"run_id": run_id, "source": "eval"},
            )
            state.mark_step("submit_eval", eval_job=eval_job)
            self._save_state(state)
            artifacts.write_json("eval_job.json", eval_job)

            trajs = envarena.get_trajs(eval_job, include_messages=True)
            state.mark_step("collect_trajs", metadata={**state.metadata, "num_trajs": len(trajs)})
            self._save_state(state)
            artifacts.write_json("trajs.json", trajs)

            dataset = research_agent.generate_dataset(
                trajs=trajs,
                memory_context=memory.get_context(ckpt=ckpt),
                current_ckpt=ckpt,
                envarena=envarena,
                model_ref=model_ref,
                harness_config=harness_config,
                traj_filter=traj_filter,
            )
            state.mark_step("generate_dataset")
            self._save_state(state)
            artifacts.write_json("dataset.json", dataset)
            self._record(memory, "dataset_created", str({"dataset_id": dataset.get("dataset_id")}), state)

            train_run = areal.train(
                base_ckpt=ckpt,
                dataset=dataset,
                method=method,
                config=train_config or {"epochs": 1},
                envarena=envarena,
                model_ref=model_ref,
                harness_config=harness_config,
            )
            state.mark_step("train", train_run=train_run)
            self._save_state(state)
            artifacts.write_json("train_run.json", train_run)
            self._record(memory, "train_finished", str(train_run), state)

            next_ckpt = str(train_run["ckpt"])
            endpoint = areal.update_rollout_endpoint(endpoint, next_ckpt)
            model_ref = envarena.update_model(model_ref, endpoint)
            state.mark_step("update_endpoint", ckpt=next_ckpt, endpoint=endpoint, model_ref=model_ref)
            self._save_state(state)
            artifacts.write_json("updated_endpoint.json", endpoint)
            artifacts.write_json("updated_model_ref.json", model_ref)

            holdout_task_set = str((eval_gate or {}).get("holdout_task_set", f"{task_set}_holdout"))
            holdout_job = envarena.submit_eval(
                model_ref=model_ref,
                task_set=holdout_task_set,
                harness_config=harness_config,
                metadata={"run_id": run_id, "source": "holdout_eval"},
            )
            holdout_trajs = envarena.get_trajs(holdout_job, include_messages=False)
            state.mark_step("holdout_eval", holdout_job=holdout_job)
            self._save_state(state)
            artifacts.write_json("holdout_job.json", holdout_job)
            artifacts.write_json("holdout_trajs.json", holdout_trajs)

            state.mark_step("record_decision_pending", status="waiting_for_acceptance")
            self._save_state(state)
            self._record(
                memory,
                "ckpt_evaluated",
                str({"ckpt": next_ckpt, "num_holdout_trajs": len(holdout_trajs)}),
                state,
            )
            return state
        except Exception as exc:
            state.mark_failed(
                {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(),
                }
            )
            self._save_state(state)
            self._record(memory, "iteration_failed", str(state.error), state)
            raise

    def get_status(self, run_id: str) -> RunState:
        return RunState.load(self._state_path(run_id))

    def resume(self, run_id: str) -> RunState:
        return self.get_status(run_id)

    def stop(self, run_id: str, reason: str | None = None) -> RunState:
        state = self.get_status(run_id)
        state.mark_stopped(reason)
        self._save_state(state)
        if self.memory is not None:
            self._record(self.memory, "run_stopped", reason or "", state)
        return state

    def _save_state(self, state: RunState) -> None:
        state.save(self._state_path(state.run_id))

    def _state_path(self, run_id: str) -> Path:
        return self.run_root / run_id / "state.json"

    def _new_run_id(self) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        return f"rsi_run_{stamp}"

    @staticmethod
    def _record(memory: Memory, title: str, content: str, state: RunState) -> None:
        memory.record(
            title,
            content,
            metadata={
                "run_id": state.run_id,
                "ckpt": state.ckpt,
                "current_step": state.current_step,
                "status": state.status,
            },
        )

    @staticmethod
    def _require(value: Any, name: str) -> Any:
        if value is None:
            raise ValueError(f"{name} is required.")
        return value
