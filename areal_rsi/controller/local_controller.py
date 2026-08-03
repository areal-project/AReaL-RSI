"""Local concrete RSIController implementation."""

from __future__ import annotations

import json
import subprocess
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from areal_rsi.config import RSIRunConfig
from areal_rsi.controller.run_state import RunState
from areal_rsi.memory.audit import AuditLogger
from areal_rsi.memory.artifacts import ArtifactStore
from areal_rsi.memory.memory import Memory
from areal_rsi.schemas.actions import Action
from areal_rsi.schemas.core import EvalRequest, TrainMethod
from areal_rsi.services.areal import AReaLService
from areal_rsi.services.env_client import EnvClient
from areal_rsi.services.research.agent import ResearchAgent
from areal_rsi.services.research.trajectory_workspace import TrajectoryWorkspace


class LocalRSIController:
    """File-backed controller with Run-scoped iterations and Memory."""

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
        if action.type == "start_loop":
            state = (
                self.run_from_config_action(action)
                if "base_config" in action.metadata
                else self.run_loop(**_normalize_start_loop_args(action.args))
            )
            return state.to_dict()
        if action.type == "resume_run":
            return self.resume(str(action.args["run_id"])).to_dict()
        if action.type == "stop_run":
            return self.stop(
                str(action.args["run_id"]),
                reason=action.args.get("reason"),
            ).to_dict()
        if action.type == "inspect_run":
            return self.inspect_run(str(action.args["run_id"]))
        raise ValueError(f"Unsupported action type: {action.type}")

    def run_iteration(
        self,
        ckpt: str,
        *,
        harness_config: dict[str, Any],
        benchmark: Any | None = None,
        task_set: Any | None = None,
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
        benchmark = _resolve_benchmark_arg(benchmark, task_set)

        run_id = _run_id or self._new_run_id()
        run_dir = self._run_dir(run_id)
        run_memory_root = run_dir / "memory"
        if memory is None or memory.root.resolve() != run_memory_root.resolve():
            # A caller cannot accidentally attach another Run's ledger.
            memory = Memory(run_memory_root)
        artifact_root = run_dir / "artifacts"
        if _artifact_prefix:
            artifact_root = artifact_root / _artifact_prefix
        artifacts = ArtifactStore(artifact_root)
        audit = AuditLogger(run_dir / "audit.jsonl")
        state = RunState(
            run_id=run_id,
            ckpt=ckpt,
            iteration=_iteration,
            status="running",
            metadata={
                **(_loop_metadata or {}),
                "iteration": _iteration,
                "benchmark": _benchmark_summary(benchmark),
                "method": method,
                "harness_config": harness_config,
                "base_ckpt": ckpt,
            },
        )
        self._save_state(state)
        self._record(
            memory,
            "iteration_started",
            f"ckpt={ckpt}, benchmark={_benchmark_summary(benchmark)}, method={method}",
            state,
        )

        try:
            model_ref = _model_ref_from_path(ckpt)

            with audit.action(
                run_id=run_id,
                iteration=state.iteration,
                step="eval",
                input={
                    "model_ref": _model_ref_summary(model_ref),
                    "benchmark": _benchmark_summary(benchmark),
                    "harness_config": harness_config,
                    "source": "eval",
                },
            ) as action:
                eval_job = envarena.submit_eval(
                    EvalRequest(
                        model=_model_asset_config(ckpt),
                        benchmark=_benchmark_ref_config(benchmark),
                        harness=_harness_ref_config(harness_config),
                        skill=_optional_asset_config(harness_config, "skill"),
                        job_config=_eval_job_config(harness_config),
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
                step="collect_trajs",
                input={"eval_job": _job_summary(eval_job), "include_messages": True},
            ) as action:
                eval_evidence = _wait_for_eval(envarena, eval_job)
                trajs = envarena.get_trajs(eval_job, include_messages=True)
                eval_score = _score_summary(trajs)
                state.mark_step(
                    "collect_trajs",
                    metadata={**state.metadata, "num_trajs": len(trajs), "last_eval_score": eval_score},
                )
                self._save_state(state)
                trajs_path = artifacts.write_json("trajs.json", trajs)
                evidence_path = artifacts.write_json(
                    "eval_evidence.json", eval_evidence
                )
                action.set_output(
                    {"num_trajs": len(trajs), "score": eval_score},
                    artifacts={
                        "eval_evidence": _artifact_ref(run_dir, evidence_path),
                        "trajs": _artifact_ref(run_dir, trajs_path),
                    },
                )
            self._record(
                memory,
                "eval_summarized",
                f"ckpt={ckpt}, benchmark={_benchmark_summary(benchmark)}, score={eval_score.get('mean_reward')}, num_trajs={len(trajs)}",
                state,
                extra_metadata={"score": eval_score, "benchmark": _benchmark_summary(benchmark)},
            )

            # Memory is physically scoped to this Run. Prior iterations in the
            # same Run are the intended context for a fresh Researcher session.
            memory_context = memory.get_context()
            research_workspace = TrajectoryWorkspace.create(
                artifacts.root / "research"
            )
            base_train_config = dict(train_config or {"epochs": 1})
            trajectory_manifest = research_workspace.materialize(
                trajs=trajs,
                memory_context=memory_context,
                run_context={
                    "run_id": run_id,
                    "iteration": state.iteration,
                    "current_ckpt": ckpt,
                    "benchmark": _benchmark_summary(benchmark),
                    "holdout_benchmark": _benchmark_summary(
                        _holdout_benchmark(benchmark, eval_gate)
                    ),
                    "method": method,
                    "harness_config": harness_config,
                },
                base_train_config=base_train_config,
            )

            with audit.action(
                run_id=run_id,
                iteration=state.iteration,
                step="generate_dataset",
                input={
                    "current_ckpt": ckpt,
                    "num_trajs": len(trajs),
                    "model_ref": _model_ref_summary(model_ref),
                    "harness_config": harness_config,
                    "traj_filter": traj_filter or {},
                },
            ) as action:
                if hasattr(research_agent, "prepare_iteration"):
                    research_result = research_agent.prepare_iteration(
                        trajectory_manifest=trajectory_manifest,
                        memory_context=memory_context,
                        current_ckpt=ckpt,
                        base_train_config=base_train_config,
                        run_context={
                            "run_id": run_id,
                            "iteration": state.iteration,
                            "benchmark": _benchmark_summary(benchmark),
                        },
                        config={"traj_filter": traj_filter or {}},
                    )
                    dataset = dict(research_result["dataset"])
                    selected_train_config = dict(
                        research_result.get("train_config") or base_train_config
                    )
                else:
                    dataset = research_agent.generate_dataset(
                        trajs=trajs,
                        memory_context=memory_context,
                        current_ckpt=ckpt,
                        envarena=envarena,
                        model_ref=model_ref,
                        harness_config=harness_config,
                        traj_filter=traj_filter,
                    )
                    research_result = {
                        "schema_version": "research-result.legacy",
                        "dataset": dataset,
                        "train_config": base_train_config,
                    }
                    selected_train_config = base_train_config
                state.mark_step(
                    "generate_dataset",
                    metadata={
                        **state.metadata,
                        "research_result": research_result,
                        "research_workspace": str(research_workspace.root),
                        "research_session_id": research_result.get("session_id"),
                    },
                )
                self._save_state(state)
                dataset_path = artifacts.write_json("dataset.json", dataset)
                research_result_path = artifacts.write_json(
                    "research_result.json", research_result
                )
                num_examples = _dataset_num_examples(dataset)
                action.set_output(
                    {
                        "dataset_id": dataset.get("dataset_id"),
                        "num_examples": num_examples,
                    },
                    artifacts={
                        "dataset": _artifact_ref(run_dir, dataset_path),
                        "research_result": _artifact_ref(
                            run_dir, research_result_path
                        ),
                    },
                )
            self._record(
                memory,
                "dataset_created",
                str(
                    {
                        "dataset_id": dataset.get("dataset_id"),
                        "num_examples": num_examples,
                    }
                ),
                state,
                extra_metadata={"dataset_id": dataset.get("dataset_id")},
            )
            self._record_research_updates(
                memory,
                research_result.get("memory_updates"),
                state,
                phase="dataset",
            )

            with audit.action(
                run_id=run_id,
                iteration=state.iteration,
                step="train",
                input={
                    "base_ckpt": ckpt,
                    "dataset_id": dataset.get("dataset_id"),
                    "method": method,
                    "config": selected_train_config,
                    "model_ref": _model_ref_summary(model_ref),
                },
            ) as action:
                train_run = _train_and_wait(
                    areal,
                    base_ckpt=ckpt,
                    dataset=dataset,
                    method=method,
                    config=selected_train_config,
                    envarena=envarena,
                    model_ref=model_ref,
                    harness_config=harness_config,
                )
                next_ckpt = str(train_run["ckpt"])
                state.mark_step("train", train_run=train_run, ckpt=next_ckpt)
                self._save_state(state)
                train_run_path = artifacts.write_json("train_run.json", train_run)
                action.set_output(
                    _train_summary(train_run),
                    artifacts={"train_run": _artifact_ref(run_dir, train_run_path)},
                )
            self._record(memory, "train_finished", str(_train_summary(train_run)), state)

            model_ref = _model_ref_from_path(next_ckpt)

            holdout_benchmark = _holdout_benchmark(benchmark, eval_gate)
            with audit.action(
                run_id=run_id,
                iteration=state.iteration,
                step="holdout_eval",
                input={
                    "model_ref": _model_ref_summary(model_ref),
                    "benchmark": _benchmark_summary(holdout_benchmark),
                    "harness_config": harness_config,
                    "source": "holdout_eval",
                },
            ) as action:
                holdout_job = envarena.submit_eval(
                    EvalRequest(
                        model=_model_asset_config(next_ckpt),
                        benchmark=_benchmark_ref_config(holdout_benchmark),
                        harness=_harness_ref_config(harness_config),
                        skill=_optional_asset_config(harness_config, "skill"),
                        job_config=_eval_job_config(harness_config),
                        metadata={"run_id": run_id, "source": "holdout_eval"},
                    )
                )
                holdout_evidence = _wait_for_eval(envarena, holdout_job)
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
                holdout_evidence_path = artifacts.write_json(
                    "holdout_evidence.json", holdout_evidence
                )
                action.set_output(
                    {
                        "holdout_job": _job_summary(holdout_job),
                        "num_trajs": len(holdout_trajs),
                        "score": holdout_score,
                    },
                    artifacts={
                        "holdout_job": _artifact_ref(run_dir, holdout_job_path),
                        "holdout_trajs": _artifact_ref(run_dir, holdout_trajs_path),
                        "holdout_evidence": _artifact_ref(
                            run_dir, holdout_evidence_path
                        ),
                    },
                )
            self._record(
                memory,
                "holdout_summarized",
                (
                    f"ckpt={next_ckpt}, benchmark={_benchmark_summary(holdout_benchmark)}, "
                    f"score={holdout_score.get('mean_reward')}, num_trajs={len(holdout_trajs)}"
                ),
                state,
                extra_metadata={"score": holdout_score, "benchmark": _benchmark_summary(holdout_benchmark)},
            )

            with audit.action(
                run_id=run_id,
                iteration=state.iteration,
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
        harness_config: dict[str, Any],
        benchmark: Any | None = None,
        task_set: Any | None = None,
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
        _run_id: str | None = None,
    ) -> RunState:
        """Run one user-started Run containing multiple RSI iterations."""

        if max_iterations < 1:
            raise ValueError("max_iterations must be >= 1.")
        benchmark = _resolve_benchmark_arg(benchmark, task_set)

        research_agent = research_agent or self._require(
            self.research_agent, "ResearchAgent"
        )
        run_id = _run_id or self._new_run_id()
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        # A Run owns one ledger shared by all of its iterations. Controller- or
        # process-global Memory cannot leak information from another Run.
        memory = Memory(run_dir / "memory")
        audit = AuditLogger(run_dir / "audit.jsonl")
        ckpt = initial_ckpt
        accepted_ckpts = [initial_ckpt]
        train_runs: list[dict[str, Any]] = [
            {
                "train_run_id": "initial-checkpoint",
                "method": method,
                "base_ckpt": initial_ckpt,
                "ckpt": initial_ckpt,
                "metrics": {},
            }
        ]
        iteration_results: list[dict[str, Any]] = []
        previous_holdout_score: dict[str, Any] | None = None
        state: RunState | None = None
        audit.record_event(
            run_id=run_id,
            event_type="user_goal_received",
            input={
                "goal": f"Improve {initial_ckpt} on {_benchmark_summary(benchmark)}",
                "initial_ckpt": initial_ckpt,
                "benchmark": _benchmark_summary(benchmark),
                "method": method,
                "max_iterations": max_iterations,
                "harness_config": harness_config,
                "stop_criteria": stop_criteria or {},
                "train_config": train_config or {},
                "traj_filter": traj_filter or {},
                "eval_gate": eval_gate or {},
            },
            output={
                "loop_spec": {
                    "initial_ckpt": initial_ckpt,
                    "benchmark": _benchmark_summary(benchmark),
                    "method": method,
                    "max_iterations": max_iterations,
                }
            },
        )

        try:
            for iteration in range(1, max_iterations + 1):
                state = self.run_iteration(
                    ckpt=ckpt,
                    benchmark=benchmark,
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
                    _loop_metadata={
                        "loop": True,
                        "initial_ckpt": initial_ckpt,
                        "max_iterations": max_iterations,
                        "accepted_ckpts": list(accepted_ckpts),
                        "iteration_results": list(iteration_results),
                        "stop_criteria": stop_criteria or {},
                    },
                )

                candidate_ckpt = str(state.metadata["candidate_ckpt"])
                if state.train_run is not None:
                    train_runs.append(state.train_run)
                holdout_score = state.metadata.get("last_holdout_score", {})
                decision = _decide_next_iteration(
                    holdout_score=holdout_score,
                    previous_holdout_score=previous_holdout_score,
                    stop_criteria=stop_criteria or {},
                    is_last_iteration=iteration == max_iterations,
                )
                audit.record_event(
                    run_id=run_id,
                    iteration=iteration,
                    event_type="loop_decision",
                    step="loop_decision",
                    input={
                        "previous_ckpt": ckpt,
                        "candidate_ckpt": candidate_ckpt,
                        "previous_holdout_score": previous_holdout_score,
                        "candidate_holdout_score": holdout_score,
                        "stop_criteria": stop_criteria or {},
                        "is_last_iteration": iteration == max_iterations,
                    },
                    output={"decision": decision},
                )
                checkpoint_selection: dict[str, Any] | None = None
                next_iteration_ckpt = candidate_ckpt
                if not decision["stop"]:
                    allowed_checkpoints = [str(run["ckpt"]) for run in train_runs]
                    research_result = state.metadata.get("research_result", {})
                    if hasattr(research_agent, "select_checkpoint"):
                        checkpoint_selection = research_agent.select_checkpoint(
                            research_result=research_result,
                            allowed_checkpoints=allowed_checkpoints,
                            evaluation={
                                "holdout_score": holdout_score,
                                "previous_holdout_score": previous_holdout_score,
                            },
                            memory_context=memory.get_context(),
                            run_context={
                                "run_id": run_id,
                                "iteration": iteration,
                            },
                        )
                        next_iteration_ckpt = str(
                            checkpoint_selection["next_checkpoint"]
                        )
                    else:
                        next_iteration_ckpt = str(
                            research_agent.choose_ckpt(
                                train_runs,
                                memory.get_context(),
                            )
                        )
                        checkpoint_selection = {
                            "schema_version": "checkpoint-decision.legacy",
                            "next_checkpoint": next_iteration_ckpt,
                            "reason": "ResearchAgent.choose_ckpt",
                        }
                    if next_iteration_ckpt not in allowed_checkpoints:
                        raise ValueError(
                            "ResearchAgent selected a checkpoint outside the allowed "
                            f"set: {next_iteration_ckpt!r}."
                        )
                    decision["next_checkpoint"] = next_iteration_ckpt
                    self._record_research_updates(
                        memory,
                        checkpoint_selection.get("memory_updates"),
                        state,
                        phase="checkpoint_selection",
                    )

                iteration_result = {
                    "iteration": iteration,
                    "base_ckpt": ckpt,
                    "candidate_ckpt": candidate_ckpt,
                    "eval_score": state.metadata.get("last_eval_score"),
                    "holdout_score": holdout_score,
                    "decision": decision,
                    "checkpoint_selection": checkpoint_selection,
                }
                iteration_results.append(iteration_result)
                ckpt = (
                    candidate_ckpt
                    if decision["stop"]
                    else next_iteration_ckpt
                )
                accepted_ckpts.append(ckpt)
                previous_holdout_score = holdout_score
                state.status = "completed"
                state.current_step = (
                    "loop_completed" if decision["stop"] else "iteration_completed"
                )
                state.ckpt = ckpt
                state.metadata = {
                    **state.metadata,
                    "loop": True,
                    "initial_ckpt": initial_ckpt,
                    "final_ckpt": ckpt,
                    "accepted_ckpts": list(accepted_ckpts),
                    "iteration_results": list(iteration_results),
                    "iteration_result": iteration_result,
                    "decision": decision,
                    "stop_reason": decision["reason"] if decision["stop"] else None,
                    "goal_reached": bool(decision.get("goal_reached", False)),
                }
                self._save_state(state)
                self._record(
                    memory,
                    "iteration_finished",
                    str(iteration_result),
                    state,
                    extra_metadata={
                        "decision": decision,
                        "candidate_ckpt": candidate_ckpt,
                    },
                )

                if decision["stop"]:
                    break

            if state is None:
                raise RuntimeError("run_loop did not execute any iterations.")

            self._record(
                memory,
                "run_completed",
                str(
                    {
                        "run_id": run_id,
                        "initial_ckpt": initial_ckpt,
                        "final_ckpt": ckpt,
                        "iterations": len(iteration_results),
                        "stop_reason": state.metadata["stop_reason"],
                    }
                ),
                state,
            )
            return state
        except Exception as exc:
            if state is not None and state.status != "failed":
                state.mark_failed(
                    {
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "traceback": traceback.format_exc(),
                    }
                )
                self._save_state(state)
                self._record(memory, "run_failed", str(state.error), state)
            raise

    def run_config(
        self,
        config: RSIRunConfig,
        *,
        config_metadata: dict[str, Any] | None = None,
    ) -> RunState:
        """Create one Run and execute its iterations from normalized config."""

        run_id = self._new_run_id()
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        config_path = run_dir / "resolved_rsi_config.json"
        config_path.write_text(json.dumps(config.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        config_metadata = config_metadata or {}
        AuditLogger(run_dir / "audit.jsonl").record_event(
            run_id=run_id,
            event_type="run_config_resolved",
            input=config_metadata,
            output={
                "resolved_config_path": str(config_path.relative_to(run_dir)),
                "normalized_config": config.to_dict(),
            },
        )
        state = self.run_loop(**config.to_run_loop_kwargs(), _run_id=run_id)
        state.metadata = {
            **state.metadata,
            "resolved_config": str(config_path.relative_to(run_dir)),
            "config": {
                **config_metadata,
                "resolved_config_path": str(config_path),
            },
        }
        self._save_state(state)
        return state

    def run_from_config_action(self, action: Action) -> RunState:
        """Execute a start_loop action against the base config in action metadata."""

        base_config = action.metadata.get("base_config")
        if not isinstance(base_config, RSIRunConfig):
            raise ValueError("start_loop config execution requires an RSIRunConfig base_config.")
        overrides = _start_loop_overrides(action.args)
        resolved = base_config.with_overrides(overrides)
        return self.run_config(
            resolved,
            config_metadata={
                "base_config_path": str(base_config.source_path) if base_config.source_path else None,
                "base_config_hash": base_config.source_hash,
                "overrides": overrides,
            },
        )

    def get_status(self, run_id: str) -> RunState:
        return RunState.load(self._state_path(run_id))

    def resume(self, run_id: str) -> RunState:
        return self.get_status(run_id)

    def stop(self, run_id: str, reason: str | None = None) -> RunState:
        state = self.get_status(run_id)
        state.mark_stopped(reason)
        self._save_state(state)
        return state

    def inspect_run(self, run_id: str) -> dict[str, Any]:
        state = self.get_status(run_id)
        return {
            "run_id": state.run_id,
            "status": state.status,
            "iteration": state.iteration,
            "current_step": state.current_step,
            "ckpt": state.ckpt,
            "metadata": state.metadata,
        }

    def _load_state(self, run_id: str) -> RunState:
        return self.get_status(run_id)

    def _save_state(self, state: RunState) -> None:
        state.save(self._state_path(state.run_id))

    def _run_dir(self, run_id: str) -> Path:
        return self.run_root / run_id

    def _state_path(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "state.json"

    def _new_run_id(self) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        return f"run_{stamp}"

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
    def _record_research_updates(
        memory: Memory,
        updates: Any,
        state: RunState,
        *,
        phase: str,
    ) -> None:
        if not isinstance(updates, list):
            return
        for index, update in enumerate(updates):
            if not isinstance(update, dict):
                continue
            title = str(update.get("title") or f"research_update_{index}")
            content = str(update.get("content") or "")
            metadata = update.get("metadata")
            memory.record(
                title,
                content,
                metadata={
                    "run_id": state.run_id,
                    "iteration": state.iteration,
                    "ckpt": state.ckpt,
                    "phase": phase,
                    **(metadata if isinstance(metadata, dict) else {}),
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


def _normalize_start_loop_args(args: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_run_iteration_args(args)
    for alias in ("ckpt", "checkpoint", "initial_checkpoint"):
        if "initial_ckpt" not in normalized and alias in normalized:
            normalized["initial_ckpt"] = normalized.pop(alias)
    for alias in ("task_set", "dataset", "benchmark"):
        if "benchmark" not in normalized and alias in normalized:
            normalized["benchmark"] = normalized.pop(alias)
    if "max_iterations" not in normalized and "iterations" in normalized:
        normalized["max_iterations"] = normalized.pop("iterations")
    if "holdout_benchmark" in normalized:
        normalized.setdefault("eval_gate", {})["holdout_benchmark"] = normalized.pop(
            "holdout_benchmark"
        )
    if "target_mean_reward" in normalized:
        normalized.setdefault("stop_criteria", {})["target_mean_reward"] = normalized.pop(
            "target_mean_reward"
        )
    normalized.pop("goal", None)
    allowed = {
        "initial_ckpt",
        "benchmark",
        "task_set",
        "harness_config",
        "method",
        "max_iterations",
        "stop_criteria",
        "train_config",
        "traj_filter",
        "eval_gate",
    }
    return {key: value for key, value in normalized.items() if key in allowed}


def _start_loop_overrides(args: dict[str, Any]) -> dict[str, Any]:
    overrides = dict(args.get("overrides") or {})
    if not isinstance(overrides, dict):
        raise ValueError("start_loop overrides must be a mapping.")
    for alias in ("initial_ckpt", "ckpt", "checkpoint", "initial_checkpoint"):
        if alias in args:
            overrides["run.initial_ckpt"] = args[alias]
            break
    for alias in ("benchmark", "task_set", "dataset"):
        if alias in args:
            overrides["env.benchmark"] = args[alias]
            break
    if "method" in args:
        overrides["run.method"] = args["method"]
    for alias in ("max_iterations", "iterations"):
        if alias in args:
            overrides["run.max_iterations"] = args[alias]
            break
    if "harness" in args:
        overrides["env.harness_config.harness"] = args["harness"]
    if "harness_config" in args:
        overrides["env.harness_config"] = args["harness_config"]
    if "train_config" in args:
        overrides["train.config"] = args["train_config"]
    if "stop_criteria" in args:
        overrides["stop_criteria"] = args["stop_criteria"]
    if "target_mean_reward" in args:
        overrides["stop_criteria.target_mean_reward"] = args[
            "target_mean_reward"
        ]
    if "holdout_benchmark" in args:
        overrides["env.holdout_benchmark"] = args["holdout_benchmark"]
    if "traj_filter" in args:
        overrides["research.traj_filter"] = args["traj_filter"]
    if isinstance(args.get("eval_gate"), dict):
        if "holdout_benchmark" in args["eval_gate"]:
            overrides["env.holdout_benchmark"] = args["eval_gate"]["holdout_benchmark"]
        elif "holdout_task_set" in args["eval_gate"]:
            overrides["env.holdout_benchmark"] = args["eval_gate"]["holdout_task_set"]
    return overrides


def _artifact_ref(run_dir: Path, path: Path) -> str:
    return str(path.relative_to(run_dir))


def _model_ref_from_path(model_path: str) -> dict[str, Any]:
    return {"model_path": model_path, "metadata": {}}


def _model_asset_config(model_path: str) -> dict[str, Any]:
    return {"type": "new", "model_path": model_path}


def _resolve_benchmark_arg(benchmark: Any | None, task_set: Any | None) -> Any:
    resolved = benchmark if benchmark is not None else task_set
    if resolved is None:
        raise ValueError("benchmark is required.")
    return resolved


def _benchmark_ref_config(benchmark: Any) -> dict[str, Any] | str:
    if isinstance(benchmark, dict):
        return dict(benchmark)
    return str(benchmark)


def _harness_ref_config(harness_config: dict[str, Any]) -> dict[str, Any] | str:
    if isinstance(harness_config.get("harness_asset"), dict):
        return dict(harness_config["harness_asset"])
    if isinstance(harness_config.get("harness_config"), dict):
        return dict(harness_config["harness_config"])
    if "harness_ref" in harness_config:
        return harness_config["harness_ref"]
    if "harness" in harness_config:
        return harness_config["harness"]
    if harness_config.get("type") in {"new", "ref"}:
        return dict(harness_config)
    return "mock_harness"


def _benchmark_summary(benchmark: Any) -> Any:
    if isinstance(benchmark, dict):
        if "ref" in benchmark:
            return benchmark["ref"]
        if "registry_ref" in benchmark:
            return benchmark["registry_ref"]
        if "key" in benchmark and "version" in benchmark:
            return f"{benchmark['key']}@{benchmark['version']}"
        return dict(benchmark)
    return str(benchmark)


def _holdout_benchmark(benchmark: Any, eval_gate: dict[str, Any] | None) -> Any:
    gate = eval_gate or {}
    if "holdout_benchmark" in gate:
        return gate["holdout_benchmark"]
    if "holdout_task_set" in gate:
        return gate["holdout_task_set"]
    return f"{_benchmark_summary(benchmark)}_holdout"


def _optional_asset_config(harness_config: dict[str, Any], name: str) -> dict[str, Any] | None:
    config_key = f"{name}_config"
    asset_key = f"{name}_asset"
    ref_key = f"{name}_ref"
    if isinstance(harness_config.get(asset_key), dict):
        return dict(harness_config[asset_key])
    if isinstance(harness_config.get(config_key), dict):
        return dict(harness_config[config_key])
    if harness_config.get(ref_key) is not None:
        return {"type": "ref", "ref": harness_config[ref_key]}
    return None


def _eval_job_config(harness_config: dict[str, Any]) -> dict[str, Any]:
    job_config: dict[str, Any] = {}
    for field_name in (
        "job_id",
        "sample_numbers",
        "concurrency_policy",
        "tags",
        "model_parameters",
        "model_env",
    ):
        if field_name in harness_config:
            job_config[field_name] = harness_config[field_name]
    if isinstance(harness_config.get("job_config"), dict):
        job_config.update(harness_config["job_config"])
    return job_config


def _wait_for_eval(
    envarena: EnvClient,
    job: dict[str, Any],
) -> dict[str, Any]:
    """Wait for an EnvClient job without extending the service interface."""

    job_config = job.get("job_config", {})
    timeout_seconds = float(job_config.get("timeout_seconds", 7200))
    poll_interval_seconds = float(job_config.get("poll_interval_seconds", 5))
    started = time.monotonic()
    while True:
        progress = envarena.get_eval_progress(job)
        status = _eval_status(progress, job)
        if status in {"DONE", "COMPLETED", "SUCCEEDED", "SUCCESS"}:
            return {
                "progress": progress,
                "result": envarena.get_eval_result(job),
            }
        if status in {
            "FAILED",
            "TIMEOUT",
            "CANCELLED",
            "STOPPED",
            "ERROR",
        }:
            raise RuntimeError(
                f"EnvArena evaluation {job.get('eval_job_id') or job.get('job_id')} "
                f"ended with status {status}."
            )
        if time.monotonic() - started >= timeout_seconds:
            raise TimeoutError(
                f"EnvArena evaluation {job.get('eval_job_id') or job.get('job_id')} "
                f"did not finish within {timeout_seconds:g} seconds."
            )
        time.sleep(max(0.0, poll_interval_seconds))


def _eval_status(progress: dict[str, Any], job: dict[str, Any]) -> str:
    nested_job = progress.get("job")
    if isinstance(nested_job, dict) and nested_job.get("status") is not None:
        value = nested_job["status"]
    else:
        value = progress.get("status", job.get("status", "PENDING"))
    return str(value).upper()


def _train_and_wait(
    areal: Any,
    *,
    base_ckpt: str,
    dataset: dict[str, Any],
    method: TrainMethod,
    config: dict[str, Any],
    envarena: EnvClient,
    model_ref: dict[str, Any],
    harness_config: dict[str, Any],
) -> dict[str, Any]:
    """Run local/mock training or wait for an asynchronous Slurm submission."""

    if not hasattr(areal, "start_experiment"):
        return areal.train(
            base_ckpt=base_ckpt,
            dataset=dataset,
            method=method,
            config=config,
            envarena=envarena,
            model_ref=model_ref,
            harness_config=harness_config,
        )

    if method != "sft":
        raise ValueError(
            f"Configured AReaL asynchronous training only supports sft, got {method!r}."
        )

    submission = areal.start_experiment(config)
    if submission.get("ckpt"):
        return submission
    job_id = str(submission.get("job_id") or "").strip()
    if not job_id:
        raise RuntimeError(
            "AReaL asynchronous training submission returned neither ckpt nor job_id."
        )
    _wait_for_slurm_job(
        job_id,
        timeout_seconds=float(config.get("job_timeout_seconds", 14400)),
        poll_interval_seconds=float(config.get("poll_interval_seconds", 30)),
    )
    artifacts = areal.get_experiment_artifacts(config)
    checkpoint = artifacts.get("latest_checkpoint")
    if not checkpoint:
        raise RuntimeError(
            f"Slurm job {job_id} completed but AReaL reported no checkpoint."
        )
    return {
        "train_run_id": submission.get("train_run_id", f"slurm:{job_id}"),
        "method": method,
        "base_ckpt": base_ckpt,
        "ckpt": str(checkpoint),
        "metrics": {},
        "metadata": {
            **submission,
            "status": "completed",
            "artifacts": artifacts,
        },
    }


def _wait_for_slurm_job(
    job_id: str,
    *,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> None:
    started = time.monotonic()
    while True:
        status = _slurm_job_state(job_id)
        if status == "COMPLETED":
            return
        if status in {
            "BOOT_FAIL",
            "CANCELLED",
            "DEADLINE",
            "FAILED",
            "NODE_FAIL",
            "OUT_OF_MEMORY",
            "PREEMPTED",
            "TIMEOUT",
        }:
            raise RuntimeError(f"Slurm job {job_id} ended with status {status}.")
        if time.monotonic() - started >= timeout_seconds:
            raise TimeoutError(
                f"Slurm job {job_id} did not finish within {timeout_seconds:g} seconds."
            )
        time.sleep(max(0.0, poll_interval_seconds))


def _slurm_job_state(job_id: str) -> str:
    sacct = subprocess.run(
        ["sacct", "-j", job_id, "--format=State", "--noheader", "--parsable2"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if sacct.returncode == 0:
        states = [
            line.split("|", 1)[0].strip().split("+", 1)[0].upper()
            for line in sacct.stdout.splitlines()
            if line.strip()
        ]
        if states:
            return states[0]
    squeue = subprocess.run(
        ["squeue", "-h", "-j", job_id, "-o", "%T"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if squeue.returncode == 0 and squeue.stdout.strip():
        return squeue.stdout.strip().splitlines()[0].upper().replace(" ", "_")
    return "UNKNOWN"


def _model_ref_summary(model_ref: dict[str, Any]) -> dict[str, Any]:
    return {
        "env_model_id": model_ref.get("env_model_id"),
        "model_ref": model_ref.get("model_ref"),
        "model_path": model_ref.get("model_path"),
        "model_name": model_ref.get("model_name"),
        "key": model_ref.get("key"),
        "version": model_ref.get("version"),
        "metadata": model_ref.get("metadata", {}),
    }


def _job_summary(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "eval_job_id": job.get("eval_job_id"),
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "benchmark": job.get("benchmark"),
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


def _dataset_num_examples(dataset: dict[str, Any]) -> int:
    """Report materialized JSONL size without loading records into Dataset.examples."""

    metadata = dataset.get("metadata")
    validation = metadata.get("validation") if isinstance(metadata, dict) else None
    record_count = validation.get("record_count") if isinstance(validation, dict) else None
    if isinstance(record_count, int) and record_count >= 0:
        return record_count
    examples = dataset.get("examples", [])
    return len(examples) if isinstance(examples, list) else 0


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

    target = stop_criteria.get(
        "target_mean_reward",
        stop_criteria.get("target"),
    )
    if target is not None and float(mean_reward) >= float(target):
        return {
            "accept": True,
            "stop": True,
            "reason": "target_mean_reward_reached",
            "goal_reached": True,
            "score": float(mean_reward),
            "target": float(target),
        }

    if is_last_iteration:
        return {
            "accept": True,
            "stop": True,
            "reason": "max_iterations_exhausted",
            "goal_reached": False,
        }

    return {
        "accept": True,
        "stop": False,
        "reason": "continue",
        "goal_reached": False,
    }
