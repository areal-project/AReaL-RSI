"""Local concrete RSIController implementation."""

from __future__ import annotations

import json
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
        memory = memory or self._require(self.memory, "Memory")
        benchmark = _resolve_benchmark_arg(benchmark, task_set)

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
                "benchmark": _benchmark_summary(benchmark),
                "method": method,
                "harness_config": harness_config,
                "base_ckpt": ckpt,
            },
        )
        self._save_state(state)
        self._record(memory, "iteration_started", f"ckpt={ckpt}, benchmark={_benchmark_summary(benchmark)}, method={method}", state)

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
                f"ckpt={ckpt}, benchmark={_benchmark_summary(benchmark)}, score={eval_score.get('mean_reward')}, num_trajs={len(trajs)}",
                state,
                extra_metadata={"score": eval_score, "benchmark": _benchmark_summary(benchmark)},
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
        """Run multiple RSI iterations under one run id."""

        if max_iterations < 1:
            raise ValueError("max_iterations must be >= 1.")
        benchmark = _resolve_benchmark_arg(benchmark, task_set)

        memory = memory or self._require(self.memory, "Memory")
        run_id = _run_id or self._new_run_id()
        run_dir = self.run_root / run_id
        audit = AuditLogger(run_dir / "audit.jsonl")
        ckpt = initial_ckpt
        accepted_ckpts = [initial_ckpt]
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

    def run_config(
        self,
        config: RSIRunConfig,
        *,
        config_metadata: dict[str, Any] | None = None,
    ) -> RunState:
        """Run an RSI loop from a normalized run config."""

        run_id = self._new_run_id()
        run_dir = self.run_root / run_id
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
        return self._load_state(run_id)

    def resume(self, run_id: str) -> RunState:
        return self.get_status(run_id)

    def stop(self, run_id: str, reason: str | None = None) -> RunState:
        state = self.get_status(run_id)
        state.mark_stopped(reason)
        self._save_state(state)
        if self.memory is not None:
            self._record(self.memory, "run_stopped", reason or "", state)
        return state

    def inspect_run(self, run_id: str) -> dict[str, Any]:
        state = self.get_status(run_id)
        return {
            "run_id": run_id,
            "status": state.status,
            "iteration": state.iteration,
            "current_step": state.current_step,
            "ckpt": state.ckpt,
            "metadata": state.metadata,
        }

    def _load_state(self, run_id: str) -> RunState:
        return RunState.load(self._state_path(run_id))

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
