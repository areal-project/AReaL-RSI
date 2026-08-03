"""Claude Agent SDK implementation of the iteration-scoped ResearchAgent."""

from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

import yaml

from areal_rsi.schemas.core import (
    Dataset,
    MemoryContext,
    ModelRef,
    TrainRun,
    Traj,
)

from .prompts import (
    RESEARCH_SYSTEM_PROMPT,
    checkpoint_selection_prompt,
    dataset_construction_prompt,
    dataset_format_repair_prompt,
    iteration_bootstrap_prompt,
    task_analysis_prompt,
)
from .sft_format import (
    SFTFormatError,
    describe_sft_reference,
    validate_sft_jsonl,
)


@dataclass(frozen=True, slots=True)
class ClaudeResearchConfig:
    """Runtime options for one-session-per-iteration Claude research."""

    model: str = "claude-opus-4-8"
    max_retries: int = 10
    retry_backoff_seconds: float = 2.0
    retry_max_backoff_seconds: float = 30.0
    max_turns: int = 80
    max_budget_usd: float | None = None
    setting_sources: tuple[str, ...] = ("user", "project", "local")
    allowed_tools: tuple[str, ...] = ("Read", "Write", "Edit", "Glob", "Grep")
    cli_path: str | None = None
    home: str | None = "/home/admin"
    reference_sft_path: str | None = None
    train_template_path: str | None = None
    format_repair_attempts: int = 3

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> "ClaudeResearchConfig":
        payload = dict(value or {})
        defaults = cls()
        return cls(
            model=str(payload.get("model", defaults.model)),
            max_retries=int(payload.get("max_retries", defaults.max_retries)),
            retry_backoff_seconds=float(
                payload.get(
                    "retry_backoff_seconds",
                    defaults.retry_backoff_seconds,
                )
            ),
            retry_max_backoff_seconds=float(
                payload.get(
                    "retry_max_backoff_seconds",
                    defaults.retry_max_backoff_seconds,
                )
            ),
            max_turns=int(payload.get("max_turns", defaults.max_turns)),
            max_budget_usd=(
                None
                if payload.get("max_budget_usd") is None
                else float(payload["max_budget_usd"])
            ),
            setting_sources=tuple(
                str(item)
                for item in payload.get("setting_sources", defaults.setting_sources)
            ),
            allowed_tools=tuple(
                str(item)
                for item in payload.get("allowed_tools", defaults.allowed_tools)
            ),
            cli_path=(
                None if payload.get("cli_path") is None else str(payload["cli_path"])
            ),
            home=(
                defaults.home
                if "home" not in payload
                else (None if payload["home"] is None else str(payload["home"]))
            ),
            reference_sft_path=(
                None
                if payload.get("reference_sft_path") is None
                else str(payload["reference_sft_path"])
            ),
            train_template_path=(
                None
                if payload.get("train_template_path") is None
                else str(payload["train_template_path"])
            ),
            format_repair_attempts=int(
                payload.get("format_repair_attempts", defaults.format_repair_attempts)
            ),
        )

    def validate(self) -> None:
        if self.max_retries < 1:
            raise ValueError("Claude Researcher max_retries must be >= 1.")
        if self.max_turns < 1:
            raise ValueError("Claude Researcher max_turns must be >= 1.")
        if self.format_repair_attempts < 0:
            raise ValueError(
                "Claude Researcher format_repair_attempts must be non-negative."
            )
        if self.retry_backoff_seconds < 0 or self.retry_max_backoff_seconds < 0:
            raise ValueError(
                "Claude Researcher retry backoff values must be non-negative."
            )
        invalid_sources = set(self.setting_sources) - {"user", "project", "local"}
        if invalid_sources:
            raise ValueError(
                f"Unsupported Claude setting sources: {sorted(invalid_sources)}"
            )


@dataclass(frozen=True, slots=True)
class ClaudeTurnResult:
    """Normalized result of one Claude Agent SDK turn."""

    session_id: str
    result: str
    total_cost_usd: float | None = None
    num_turns: int | None = None


ClaudeQueryRunner = Callable[
    [str, Path, ClaudeResearchConfig, str | None],
    ClaudeTurnResult,
]


class ClaudeResearchAgent:
    """ResearchAgent using one resumable Claude Code session per iteration."""

    def __init__(
        self,
        config: ClaudeResearchConfig | None = None,
        *,
        query_runner: ClaudeQueryRunner | None = None,
    ) -> None:
        self.config = config or ClaudeResearchConfig()
        self.config.validate()
        self._query_runner = query_runner or _run_claude_turn

    def prepare_iteration(
        self,
        *,
        trajectory_manifest: dict[str, Any],
        memory_context: MemoryContext,
        current_ckpt: str,
        base_train_config: dict[str, Any],
        run_context: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del memory_context
        effective = _merge_config(self.config, config)
        template_path = str(
            base_train_config.get("train_config_path")
            or base_train_config.get("experiment_config")
            or effective.train_template_path
            or ""
        ).strip()
        if not template_path:
            raise ValueError(
                "Claude Researcher requires train_config.train_config_path "
                "or train_template_path."
            )
        effective = replace(effective, train_template_path=template_path)
        workspace = Path(str(trajectory_manifest["workspace"])).resolve()
        output = trajectory_manifest["output"]
        reference_path = effective.reference_sft_path
        if not reference_path:
            raise ValueError(
                "Claude Researcher requires research.reference_sft_path for SFT validation."
            )
        reference_contract = describe_sft_reference(reference_path)

        session_id: str | None = None
        bootstrap = self._query_with_retries(
            iteration_bootstrap_prompt(
                manifest=trajectory_manifest,
                reference_sft_path=reference_path,
            ),
            workspace=workspace,
            config=effective,
            session_id=session_id,
        )
        session_id = bootstrap.session_id

        completed_paths: list[str] = []
        skipped_tasks: list[dict[str, Any]] = []
        for task in trajectory_manifest.get("tasks", []):
            experience_path = str(
                Path(output["experiences_dir"]) / f"{Path(str(task['path'])).stem}.json"
            )
            try:
                turn = self._query_with_retries(
                    task_analysis_prompt(
                        task=task,
                        experience_path=experience_path,
                    ),
                    workspace=workspace,
                    config=effective,
                    session_id=session_id,
                )
                session_id = turn.session_id
                experience = _read_json(Path(experience_path), "task experience")
                _validate_task_experience(experience, expected_task_id=str(task["task_id"]))
                if experience.get("status") == "skipped":
                    skipped_tasks.append(
                        {
                            "task_id": task["task_id"],
                            "reason": experience.get("analysis", "researcher_skipped"),
                            "path": experience_path,
                        }
                    )
                else:
                    completed_paths.append(experience_path)
            except Exception as exc:
                skipped = {
                    "schema_version": "task-experience.v1",
                    "task_id": task["task_id"],
                    "status": "skipped",
                    "source_traj_ids": list(task.get("trajectory_ids", [])),
                    "experience": "",
                    "analysis": f"Task analysis failed after retries: {exc}",
                    "training_recommendation": {
                        "include": False,
                        "use_failed_trajectories": False,
                        "strategy": "skip_after_retries",
                    },
                    "metadata": {"error_type": type(exc).__name__},
                }
                _write_json(Path(experience_path), skipped)
                skipped_tasks.append(
                    {
                        "task_id": task["task_id"],
                        "reason": str(exc),
                        "path": experience_path,
                    }
                )

        synthesis = self._query_with_retries(
            dataset_construction_prompt(
                manifest=trajectory_manifest,
                completed_experience_paths=completed_paths,
                skipped_tasks=skipped_tasks,
                reference_sft_path=reference_path,
                reference_contract=reference_contract,
            ),
            workspace=workspace,
            config=effective,
            session_id=session_id,
        )
        session_id = synthesis.session_id

        result_path = Path(str(output["research_result_path"]))
        result = _read_json(result_path, "research result")
        dataset_path = Path(str(result.get("dataset_path") or output["dataset_path"]))
        validation_errors: list[str] = []
        for repair_attempt in range(effective.format_repair_attempts + 1):
            try:
                validation = validate_sft_jsonl(
                    dataset_path,
                    reference_path=reference_path,
                )
                break
            except SFTFormatError as exc:
                validation_errors.append(str(exc))
                if repair_attempt >= effective.format_repair_attempts:
                    raise
                repair = self._query_with_retries(
                    dataset_format_repair_prompt(
                        dataset_path=str(dataset_path),
                        reference_sft_path=reference_path,
                        reference_contract=reference_contract,
                        validation_error=str(exc),
                        attempt=repair_attempt + 1,
                    ),
                    workspace=workspace,
                    config=effective,
                    session_id=session_id,
                )
                session_id = repair.session_id
        else:  # pragma: no cover - the loop always validates or raises
            raise AssertionError("SFT validation loop terminated unexpectedly.")
        train_config_path = Path(
            str(result.get("train_config_path") or output["train_config_path"])
        )
        _validate_train_yaml(
            train_config_path,
            expected_ckpt=current_ckpt,
            expected_dataset=dataset_path,
        )
        train_config = {
            **base_train_config,
            "train_config_path": str(train_config_path.resolve()),
        }
        train_config.pop("experiment_config", None)
        skipped_ids = {
            str(item["task_id"]) for item in skipped_tasks if item.get("task_id")
        }
        skipped_ids.update(str(item) for item in result.get("skipped_task_ids", []))
        return {
            **result,
            "dataset": {
                "dataset_id": str(result.get("dataset_id") or dataset_path.stem),
                "jsonl_path": str(dataset_path.resolve()),
                "examples": [],
                "metadata": {
                    "format": "reference_sft_jsonl",
                    "validation": validation,
                    "included_task_ids": list(result.get("included_task_ids", [])),
                    "skipped_task_ids": sorted(skipped_ids),
                },
            },
            "train_config": train_config,
            "train_config_path": str(train_config_path.resolve()),
            "session_id": session_id,
            "completed_experience_paths": completed_paths,
            "skipped_tasks": skipped_tasks,
            "validation": validation,
            "validation_errors_repaired": validation_errors,
        }

    def select_checkpoint(
        self,
        *,
        research_result: dict[str, Any],
        allowed_checkpoints: list[str],
        evaluation: dict[str, Any],
        memory_context: MemoryContext,
        run_context: dict[str, Any],
    ) -> dict[str, Any]:
        workspace = _workspace_from_research_result(research_result)
        effective = _merge_config(self.config, run_context.get("research_config"))
        session_id = str(research_result.get("session_id") or "") or None
        evaluation_context_path = workspace / "input" / "evaluation_context.json"
        decision_path = workspace / "output" / "checkpoint_decision.json"
        _write_json(
            evaluation_context_path,
            {
                "schema_version": "research-evaluation-context.v1",
                "allowed_checkpoints": allowed_checkpoints,
                "evaluation": evaluation,
                "memory_context": memory_context,
                "run_context": run_context,
            },
        )
        turn = self._query_with_retries(
            checkpoint_selection_prompt(
                evaluation_context_path=str(evaluation_context_path),
                decision_path=str(decision_path),
            ),
            workspace=workspace,
            config=effective,
            session_id=session_id,
        )
        decision = _read_json(decision_path, "checkpoint decision")
        selected = str(decision.get("next_checkpoint") or "")
        if selected not in allowed_checkpoints:
            raise ValueError(
                f"Researcher selected unknown checkpoint {selected!r}; "
                f"allowed={allowed_checkpoints!r}"
            )
        return {**decision, "session_id": turn.session_id}

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
        del trajs, memory_context, current_ckpt, envarena, model_ref, harness_config
        del traj_filter, config
        raise RuntimeError(
            "ClaudeResearchAgent requires the file-backed prepare_iteration API."
        )

    def choose_ckpt(
        self, train_runs: list[TrainRun], memory_context: MemoryContext
    ) -> str:
        del memory_context
        if not train_runs:
            raise ValueError("No train runs available for checkpoint selection.")
        return str(train_runs[-1]["ckpt"])

    def _query_with_retries(
        self,
        prompt: str,
        *,
        workspace: Path,
        config: ClaudeResearchConfig,
        session_id: str | None,
    ) -> ClaudeTurnResult:
        last_error: Exception | None = None
        for attempt in range(1, config.max_retries + 1):
            try:
                result = self._query_runner(prompt, workspace, config, session_id)
                if not result.session_id:
                    raise RuntimeError("Claude Agent SDK returned no session_id.")
                return result
            except Exception as exc:
                last_error = exc
                if attempt >= config.max_retries:
                    break
                delay = min(
                    config.retry_max_backoff_seconds,
                    config.retry_backoff_seconds * (2 ** (attempt - 1)),
                )
                if delay:
                    time.sleep(delay * random.uniform(0.75, 1.25))
        assert last_error is not None
        raise RuntimeError(
            f"Claude Agent SDK failed after {config.max_retries} attempts: {last_error}"
        ) from last_error


def _merge_config(
    base: ClaudeResearchConfig,
    overrides: dict[str, Any] | None,
) -> ClaudeResearchConfig:
    if not overrides:
        return base
    values = {
        field: value
        for field, value in overrides.items()
        if field in ClaudeResearchConfig.__dataclass_fields__
    }
    for tuple_field in ("setting_sources", "allowed_tools"):
        if tuple_field in values:
            values[tuple_field] = tuple(str(item) for item in values[tuple_field])
    merged = replace(base, **values)
    merged.validate()
    return merged


def _run_claude_turn(
    prompt: str,
    workspace: Path,
    config: ClaudeResearchConfig,
    session_id: str | None,
) -> ClaudeTurnResult:
    return asyncio.run(_run_claude_turn_async(prompt, workspace, config, session_id))


async def _run_claude_turn_async(
    prompt: str,
    workspace: Path,
    config: ClaudeResearchConfig,
    session_id: str | None,
) -> ClaudeTurnResult:
    try:
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage
    except ImportError as exc:
        raise RuntimeError(
            "Claude Researcher requires `claude-agent-sdk`. "
            "Install with `pip install -e '.[researcher]'`."
        ) from exc

    add_dirs = []
    for configured_path in (config.reference_sft_path, config.train_template_path):
        if configured_path:
            add_dirs.append(Path(configured_path).expanduser().resolve().parent)
    options = ClaudeAgentOptions(
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": RESEARCH_SYSTEM_PROMPT,
        },
        model=config.model,
        max_turns=config.max_turns,
        max_budget_usd=config.max_budget_usd,
        cwd=workspace,
        add_dirs=add_dirs,
        cli_path=config.cli_path,
        allowed_tools=list(config.allowed_tools),
        tools=list(config.allowed_tools),
        permission_mode="acceptEdits",
        setting_sources=list(config.setting_sources),
        env=({"HOME": config.home} if config.home else {}),
        resume=session_id,
    )
    result_message: Any | None = None
    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for message in client.receive_response():
            if isinstance(message, ResultMessage):
                result_message = message
    if result_message is None:
        raise RuntimeError("Claude Agent SDK returned no ResultMessage.")
    if result_message.is_error:
        raise RuntimeError(
            f"Claude Agent SDK turn failed: {result_message.result or result_message.subtype}"
        )
    return ClaudeTurnResult(
        session_id=str(result_message.session_id),
        result=str(result_message.result or ""),
        total_cost_usd=result_message.total_cost_usd,
        num_turns=result_message.num_turns,
    )


def _workspace_from_research_result(research_result: dict[str, Any]) -> Path:
    result_path = research_result.get("train_config_path")
    if result_path:
        path = Path(str(result_path)).resolve()
        if path.parent.name == "output":
            return path.parent.parent
    dataset = research_result.get("dataset", {})
    dataset_path = dataset.get("jsonl_path") if isinstance(dataset, dict) else None
    if dataset_path:
        path = Path(str(dataset_path)).resolve()
        if path.parent.name == "output":
            return path.parent.parent
    raise ValueError("Research result does not identify its iteration workspace.")


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"{label.capitalize()} file does not exist: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label.capitalize()} must contain a JSON object: {path}")
    return payload


def _validate_train_yaml(
    path: Path,
    *,
    expected_ckpt: str,
    expected_dataset: Path,
) -> None:
    if not path.exists():
        raise ValueError(f"Researcher training YAML does not exist: {path}")
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Researcher training YAML is invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Researcher training YAML must contain a mapping.")
    actor = payload.get("actor")
    dataset = payload.get("train_dataset")
    actor_path = actor.get("path") if isinstance(actor, dict) else None
    dataset_path = dataset.get("path") if isinstance(dataset, dict) else None
    if str(actor_path) != expected_ckpt:
        raise ValueError(
            "Researcher training YAML actor.path does not match the current "
            f"checkpoint: expected {expected_ckpt!r}, got {actor_path!r}."
        )
    if Path(str(dataset_path)).resolve() != expected_dataset.resolve():
        raise ValueError(
            "Researcher training YAML train_dataset.path does not match the "
            f"generated dataset: expected {str(expected_dataset)!r}, "
            f"got {dataset_path!r}."
        )
    cluster = payload.get("cluster")
    if not isinstance(cluster, dict):
        raise ValueError("Researcher training YAML must contain cluster settings.")
    try:
        world_size = int(cluster["n_nodes"]) * int(cluster["n_gpus_per_node"])
        batch_size = int(dataset["batch_size"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "Researcher training YAML must define integer cluster.n_nodes, "
            "cluster.n_gpus_per_node, and train_dataset.batch_size."
        ) from exc
    if world_size < 1 or batch_size < 1 or batch_size % world_size != 0:
        raise ValueError(
            "Researcher training YAML train_dataset.batch_size must be a positive "
            f"multiple of world size {world_size}; got {batch_size}."
        )


def _validate_task_experience(
    payload: dict[str, Any],
    *,
    expected_task_id: str,
) -> None:
    if payload.get("schema_version") != "task-experience.v1":
        raise ValueError("Task experience has an unsupported schema_version.")
    if str(payload.get("task_id")) != expected_task_id:
        raise ValueError(
            f"Task experience id {payload.get('task_id')!r} does not match "
            f"{expected_task_id!r}."
        )
    if payload.get("status") not in {"completed", "skipped"}:
        raise ValueError("Task experience status must be 'completed' or 'skipped'.")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=repr),
        encoding="utf-8",
    )
