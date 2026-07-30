"""Local concrete RSIController implementation."""

from __future__ import annotations

import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from areal_rsi.controller.run_state import RunState
from areal_rsi.memory.audit import AuditLogger
from areal_rsi.memory.artifacts import ArtifactStore
from areal_rsi.memory.memory import Memory
from areal_rsi.schemas.actions import Action
from areal_rsi.schemas.core import EvalRequest, TrainMethod
from areal_rsi.services.areal import AReaLService
from areal_rsi.services.env_client import EnvClient
from areal_rsi.services.research import ResearchAgent


class LocalRSIController:
    """File-backed controller for local runs and mock integration tests."""

    def __init__(
        self,
        run_root: str | Path = "runs",
        *,
        areal: AReaLService | None = None,
        envarena: EnvClient | None = None,
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
        if action.type == "run_loop":
            state = self.run_loop(**_normalize_run_loop_args(action.args))
            return state.to_dict()
        if action.type == "run_iteration":
            state = self.run_iteration(**_normalize_run_iteration_args(action.args))
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
        envarena: EnvClient | None = None,
        research_agent: ResearchAgent | None = None,
        memory: Memory | None = None,
        _run_id: str | None = None,
        _iteration: int = 1,
        _artifact_prefix: str | None = None,
        _loop_metadata: dict[str, Any] | None = None,
    ) -> RunState:
        areal = areal or self._require(self.areal, "AReaLService")
        envarena = envarena or self._require(self.envarena, "EnvClient")
        research_agent = research_agent or self._require(self.research_agent, "ResearchAgent")
        memory = memory or self._require(self.memory, "Memory")

        run_id = _run_id or self._new_run_id()
        run_dir = self.run_root / run_id
        artifacts = ArtifactStore(run_dir / "artifacts" / _artifact_prefix if _artifact_prefix else run_dir / "artifacts")
        audit = AuditLogger(run_dir / "audit.jsonl")
        state = RunState(
            run_id=run_id,
            ckpt=ckpt,
            iteration=_iteration,
            status="running",
            metadata={
                **(_loop_metadata or {}),
                "task_set": task_set,
                "method": method,
                "harness_config": harness_config,
                "base_ckpt": ckpt,
            },
        )
        self._save_state(state)
        self._record(memory, "iteration_started", f"ckpt={ckpt}, task_set={task_set}, method={method}", state)

        try:
            endpoint_config = {"backend": "mock-or-adapter"}
            with audit.action(
                run_id=run_id,
                iteration=state.iteration,
                action_type="ensure_rollout_endpoint",
                step="ensure_rollout_endpoint",
                input={"ckpt": ckpt, "config": endpoint_config},
            ) as action:
                endpoint = areal.ensure_rollout_endpoint(ckpt, config=endpoint_config)
                state.mark_step("ensure_rollout_endpoint", endpoint=endpoint)
                self._save_state(state)
                endpoint_path = artifacts.write_json("endpoint.json", endpoint)
                action.set_output(
                    {"endpoint": _endpoint_summary(endpoint)},
                    artifacts={"endpoint": _artifact_ref(run_dir, endpoint_path)},
                )

            with audit.action(
                run_id=run_id,
                iteration=state.iteration,
                action_type="register_model",
                step="register_model",
                input={"endpoint": _endpoint_summary(endpoint)},
            ) as action:
                model_ref = envarena.register_model(endpoint)
                state.mark_step("register_model", model_ref=model_ref)
                self._save_state(state)
                model_ref_path = artifacts.write_json("model_ref.json", model_ref)
                action.set_output(
                    {"model_ref": _model_ref_summary(model_ref)},
                    artifacts={"model_ref": _artifact_ref(run_dir, model_ref_path)},
                )

            with audit.action(
                run_id=run_id,
                iteration=state.iteration,
                action_type="eval",
                step="submit_eval",
                input={
                    "model_ref": _model_ref_summary(model_ref),
                    "task_set": task_set,
                    "harness_config": harness_config,
                    "source": "eval",
                },
            ) as action:
                eval_job = envarena.submit_eval(
                    EvalRequest(
                        model_ref=model_ref,
                        task_set=task_set,
                        harness_config=harness_config,
                        metadata={"run_id": run_id, "source": "eval"},
                    )
                )
                state.mark_step("submit_eval", eval_job=eval_job)
                self._save_state(state)
                eval_job_path = artifacts.write_json("eval_job.json", eval_job)
                action.set_output(
                    {"eval_job": _job_summary(eval_job)},
                    artifacts={"eval_job": _artifact_ref(run_dir, eval_job_path)},
                )

            with audit.action(
                run_id=run_id,
                iteration=state.iteration,
                action_type="collect_trajs",
                step="collect_trajs",
                input={"eval_job": _job_summary(eval_job), "include_messages": True},
            ) as action:
                trajs = envarena.get_trajs(eval_job, include_messages=True)
                eval_score = _score_summary(trajs)
                state.mark_step(
                    "collect_trajs",
                    metadata={**state.metadata, "num_trajs": len(trajs), "last_eval_score": eval_score},
                )
                self._save_state(state)
                trajs_path = artifacts.write_json("trajs.json", trajs)
                action.set_output(
                    {"num_trajs": len(trajs), "score": eval_score},
                    artifacts={"trajs": _artifact_ref(run_dir, trajs_path)},
                )
            self._record(
                memory,
                "eval_summarized",
                f"ckpt={ckpt}, task_set={task_set}, score={eval_score.get('mean_reward')}, num_trajs={len(trajs)}",
                state,
                extra_metadata={"score": eval_score, "task_set": task_set},
            )

            with audit.action(
                run_id=run_id,
                iteration=state.iteration,
                action_type="generate_dataset",
                step="generate_dataset",
                input={
                    "current_ckpt": ckpt,
                    "num_trajs": len(trajs),
                    "model_ref": _model_ref_summary(model_ref),
                    "harness_config": harness_config,
                    "traj_filter": traj_filter or {},
                },
            ) as action:
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
                dataset_path = artifacts.write_json("dataset.json", dataset)
                action.set_output(
                    {
                        "dataset_id": dataset.get("dataset_id"),
                        "num_examples": len(dataset.get("examples", [])),
                    },
                    artifacts={"dataset": _artifact_ref(run_dir, dataset_path)},
                )
            self._record(
                memory,
                "dataset_created",
                str({"dataset_id": dataset.get("dataset_id"), "num_examples": len(dataset.get("examples", []))}),
                state,
                extra_metadata={"dataset_id": dataset.get("dataset_id")},
            )

            train_config = train_config or {"epochs": 1}
            with audit.action(
                run_id=run_id,
                iteration=state.iteration,
                action_type="train",
                step="train",
                input={
                    "base_ckpt": ckpt,
                    "dataset_id": dataset.get("dataset_id"),
                    "method": method,
                    "config": train_config,
                    "model_ref": _model_ref_summary(model_ref),
                },
            ) as action:
                train_run = areal.train(
                    base_ckpt=ckpt,
                    dataset=dataset,
                    method=method,
                    config=train_config,
                    envarena=envarena,
                    model_ref=model_ref,
                    harness_config=harness_config,
                )
                state.mark_step("train", train_run=train_run)
                self._save_state(state)
                train_run_path = artifacts.write_json("train_run.json", train_run)
                action.set_output(
                    _train_summary(train_run),
                    artifacts={"train_run": _artifact_ref(run_dir, train_run_path)},
                )
            self._record(memory, "train_finished", str(_train_summary(train_run)), state)

            next_ckpt = str(train_run["ckpt"])
            with audit.action(
                run_id=run_id,
                iteration=state.iteration,
                action_type="update_endpoint",
                step="update_endpoint",
                input={
                    "previous_endpoint": _endpoint_summary(endpoint),
                    "previous_model_ref": _model_ref_summary(model_ref),
                    "next_ckpt": next_ckpt,
                },
            ) as action:
                endpoint = areal.update_rollout_endpoint(endpoint, next_ckpt)
                model_ref = envarena.update_model(model_ref, endpoint)
                state.mark_step("update_endpoint", ckpt=next_ckpt, endpoint=endpoint, model_ref=model_ref)
                self._save_state(state)
                updated_endpoint_path = artifacts.write_json("updated_endpoint.json", endpoint)
                updated_model_ref_path = artifacts.write_json("updated_model_ref.json", model_ref)
                action.set_output(
                    {
                        "endpoint": _endpoint_summary(endpoint),
                        "model_ref": _model_ref_summary(model_ref),
                    },
                    artifacts={
                        "endpoint": _artifact_ref(run_dir, updated_endpoint_path),
                        "model_ref": _artifact_ref(run_dir, updated_model_ref_path),
                    },
                )

            holdout_task_set = str((eval_gate or {}).get("holdout_task_set", f"{task_set}_holdout"))
            with audit.action(
                run_id=run_id,
                iteration=state.iteration,
                action_type="holdout_eval",
                step="holdout_eval",
                input={
                    "model_ref": _model_ref_summary(model_ref),
                    "task_set": holdout_task_set,
                    "harness_config": harness_config,
                    "source": "holdout_eval",
                },
            ) as action:
                holdout_job = envarena.submit_eval(
                    EvalRequest(
                        model_ref=model_ref,
                        task_set=holdout_task_set,
                        harness_config=harness_config,
                        metadata={"run_id": run_id, "source": "holdout_eval"},
                    )
                )
                holdout_trajs = envarena.get_trajs(holdout_job, include_messages=False)
                holdout_score = _score_summary(holdout_trajs)
                state.mark_step(
                    "holdout_eval",
                    holdout_job=holdout_job,
                    metadata={
                        **state.metadata,
                        "last_holdout_score": holdout_score,
                        "candidate_ckpt": next_ckpt,
                    },
                )
                self._save_state(state)
                holdout_job_path = artifacts.write_json("holdout_job.json", holdout_job)
                holdout_trajs_path = artifacts.write_json("holdout_trajs.json", holdout_trajs)
                action.set_output(
                    {
                        "holdout_job": _job_summary(holdout_job),
                        "num_trajs": len(holdout_trajs),
                        "score": holdout_score,
                    },
                    artifacts={
                        "holdout_job": _artifact_ref(run_dir, holdout_job_path),
                        "holdout_trajs": _artifact_ref(run_dir, holdout_trajs_path),
                    },
                )
            self._record(
                memory,
                "holdout_summarized",
                (
                    f"ckpt={next_ckpt}, task_set={holdout_task_set}, "
                    f"score={holdout_score.get('mean_reward')}, num_trajs={len(holdout_trajs)}"
                ),
                state,
                extra_metadata={"score": holdout_score, "task_set": holdout_task_set},
            )

            with audit.action(
                run_id=run_id,
                iteration=state.iteration,
                action_type="record_decision_pending",
                step="record_decision_pending",
                input={"ckpt": next_ckpt, "holdout_score": holdout_score},
            ) as action:
                state.mark_step("record_decision_pending", status="waiting_for_acceptance")
                self._save_state(state)
                action.set_output({"status": state.status, "ckpt": next_ckpt})
            self._record(
                memory,
                "ckpt_evaluated",
                str({"ckpt": next_ckpt, "num_holdout_trajs": len(holdout_trajs), "score": holdout_score}),
                state,
                extra_metadata={"score": holdout_score},
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
        areal: AReaLService | None = None,
        envarena: EnvClient | None = None,
        research_agent: ResearchAgent | None = None,
        memory: Memory | None = None,
    ) -> RunState:
        """Run multiple RSI iterations under one run id."""

        if max_iterations < 1:
            raise ValueError("max_iterations must be >= 1.")

        memory = memory or self._require(self.memory, "Memory")
        run_id = self._new_run_id()
        ckpt = initial_ckpt
        accepted_ckpts = [initial_ckpt]
        iteration_results: list[dict[str, Any]] = []
        previous_holdout_score: dict[str, Any] | None = None
        state: RunState | None = None

        for iteration in range(1, max_iterations + 1):
            loop_metadata = {
                "loop": True,
                "initial_ckpt": initial_ckpt,
                "max_iterations": max_iterations,
                "accepted_ckpts": accepted_ckpts,
                "iteration_results": iteration_results,
                "stop_criteria": stop_criteria or {},
            }
            state = self.run_iteration(
                ckpt=ckpt,
                task_set=task_set,
                harness_config=harness_config,
                method=method,
                train_config=train_config,
                traj_filter=traj_filter,
                eval_gate=eval_gate,
                areal=areal,
                envarena=envarena,
                research_agent=research_agent,
                memory=memory,
                _run_id=run_id,
                _iteration=iteration,
                _artifact_prefix=f"iter_{iteration:03d}",
                _loop_metadata=loop_metadata,
            )

            candidate_ckpt = str(state.metadata["candidate_ckpt"])
            holdout_score = state.metadata.get("last_holdout_score", {})
            decision = _decide_next_iteration(
                holdout_score=holdout_score,
                previous_holdout_score=previous_holdout_score,
                stop_criteria=stop_criteria or {},
                is_last_iteration=iteration == max_iterations,
            )
            iteration_result = {
                "iteration": iteration,
                "base_ckpt": ckpt,
                "candidate_ckpt": candidate_ckpt,
                "eval_score": state.metadata.get("last_eval_score"),
                "holdout_score": holdout_score,
                "decision": decision,
            }
            iteration_results.append(iteration_result)
            self._record(
                memory,
                "iteration_finished",
                str(iteration_result),
                state,
                extra_metadata={"decision": decision, "candidate_ckpt": candidate_ckpt},
            )

            if decision["accept"]:
                ckpt = candidate_ckpt
                accepted_ckpts.append(candidate_ckpt)
                previous_holdout_score = holdout_score

            if decision["stop"]:
                break

        if state is None:
            raise RuntimeError("run_loop did not execute any iterations.")

        state.status = "completed"
        state.current_step = "loop_completed"
        state.metadata = {
            **state.metadata,
            "loop": True,
            "initial_ckpt": initial_ckpt,
            "final_ckpt": ckpt,
            "accepted_ckpts": accepted_ckpts,
            "iteration_results": iteration_results,
            "stop_reason": iteration_results[-1]["decision"]["reason"],
        }
        self._save_state(state)
        self._record(
            memory,
            "loop_completed",
            str(
                {
                    "initial_ckpt": initial_ckpt,
                    "final_ckpt": ckpt,
                    "iterations": len(iteration_results),
                    "stop_reason": state.metadata["stop_reason"],
                }
            ),
            state,
        )
        return state

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
    def _record(
        memory: Memory,
        title: str,
        content: str,
        state: RunState,
        *,
        extra_metadata: dict[str, Any] | None = None,
    ) -> None:
        memory.record(
            title,
            content,
            metadata={
                "run_id": state.run_id,
                "iteration": state.iteration,
                "ckpt": state.ckpt,
                "current_step": state.current_step,
                "status": state.status,
                **(extra_metadata or {}),
            },
        )

    @staticmethod
    def _require(value: Any, name: str) -> Any:
        if value is None:
            raise ValueError(f"{name} is required.")
        return value


def _normalize_run_iteration_args(args: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(args)
    if "harness_config" not in normalized and "harness" in normalized:
        normalized["harness_config"] = {"harness": normalized.pop("harness")}
    if "harness_config" not in normalized:
        normalized["harness_config"] = {"harness": "mock_harness"}
    return normalized


def _normalize_run_loop_args(args: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_run_iteration_args(args)
    if "initial_ckpt" not in normalized and "ckpt" in normalized:
        normalized["initial_ckpt"] = normalized.pop("ckpt")
    return normalized


def _artifact_ref(run_dir: Path, path: Path) -> str:
    return str(path.relative_to(run_dir))


def _endpoint_summary(endpoint: dict[str, Any]) -> dict[str, Any]:
    return {
        "model_ref": endpoint.get("model_ref"),
        "base_url": endpoint.get("base_url"),
        "protocol": endpoint.get("protocol"),
        "metadata": endpoint.get("metadata", {}),
    }


def _model_ref_summary(model_ref: dict[str, Any]) -> dict[str, Any]:
    return {
        "env_model_id": model_ref.get("env_model_id"),
        "model_ref": model_ref.get("model_ref"),
        "key": model_ref.get("key"),
        "version": model_ref.get("version"),
        "metadata": model_ref.get("metadata", {}),
    }


def _job_summary(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "eval_job_id": job.get("eval_job_id"),
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "task_set": job.get("task_set"),
        "metrics": job.get("metrics", {}),
        "metadata": job.get("metadata", {}),
    }


def _train_summary(train_run: dict[str, Any]) -> dict[str, Any]:
    return {
        "train_run_id": train_run.get("train_run_id"),
        "method": train_run.get("method"),
        "base_ckpt": train_run.get("base_ckpt"),
        "ckpt": train_run.get("ckpt"),
        "metrics": train_run.get("metrics", {}),
    }


def _score_summary(trajs: list[dict[str, Any]]) -> dict[str, Any]:
    rewards = [float(traj.get("reward", 0.0)) for traj in trajs]
    if not rewards:
        return {"num_trajs": 0, "mean_reward": None, "pass_rate": None}
    return {
        "num_trajs": len(rewards),
        "mean_reward": sum(rewards) / len(rewards),
        "pass_rate": sum(1 for reward in rewards if reward >= 1.0) / len(rewards),
    }


def _decide_next_iteration(
    *,
    holdout_score: dict[str, Any],
    previous_holdout_score: dict[str, Any] | None,
    stop_criteria: dict[str, Any],
    is_last_iteration: bool,
) -> dict[str, Any]:
    mean_reward = holdout_score.get("mean_reward")
    if mean_reward is None:
        return {"accept": False, "stop": True, "reason": "missing_holdout_score"}

    min_score = stop_criteria.get("min_holdout_mean_reward")
    if min_score is not None and float(mean_reward) < float(min_score):
        return {
            "accept": False,
            "stop": True,
            "reason": "below_min_holdout_mean_reward",
            "threshold": float(min_score),
        }

    min_delta = stop_criteria.get("min_delta")
    previous_mean = None if previous_holdout_score is None else previous_holdout_score.get("mean_reward")
    if min_delta is not None and previous_mean is not None:
        delta = float(mean_reward) - float(previous_mean)
        if delta < float(min_delta):
            return {
                "accept": True,
                "stop": True,
                "reason": "below_min_delta",
                "delta": delta,
                "threshold": float(min_delta),
            }

    if is_last_iteration:
        return {"accept": True, "stop": True, "reason": "max_iterations_reached"}

    return {"accept": True, "stop": False, "reason": "continue"}
