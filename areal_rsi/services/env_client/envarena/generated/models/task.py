from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.task_status import TaskStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.error import Error
    from ..models.model_spec import ModelSpec
    from ..models.task_timings import TaskTimings
    from ..models.version_ref import VersionRef


T = TypeVar("T", bound="Task")


@_attrs_define
class Task:
    """
    Attributes:
        task_id (str):
        job_id (str):
        benchmark_ref (VersionRef):
        env_ref (VersionRef):
        harness_ref (VersionRef):
        reward_ref (VersionRef):
        model (ModelSpec): Compact model runtime config. EnvArena recognizes model_name, parameters, and env; unknown
            fields are preserved but are not used for routing.
        sample_index (int):
        status (TaskStatus):
        harness_key (str | Unset):
        env_key (str | Unset):
        model_ref (VersionRef | Unset):
        attempts (int | Unset):
        generation (int | Unset): The execution generation this task row belongs to (see
            BenchmarkJob.execution_generation).
        source_task_id (str | Unset): Set for a derived RESCORE Task; source agent products are mounted from this Task.
        source_attempt (int | Unset): Source Task attempt whose workspace/out products are reused by a RESCORE Task.
        trace_id (str | Unset):
        traceparent (str | Unset):
        score (float | None | Unset):
        workspace_uri (str | Unset):
        agent_sandbox_id (str | Unset):
        eval_sandbox_id (str | Unset):
        timings (TaskTimings | Unset):
        failure (Error | Unset):
        created_at (datetime.datetime | Unset):
        updated_at (datetime.datetime | Unset):
    """

    task_id: str
    job_id: str
    benchmark_ref: VersionRef
    env_ref: VersionRef
    harness_ref: VersionRef
    reward_ref: VersionRef
    model: ModelSpec
    sample_index: int
    status: TaskStatus
    harness_key: str | Unset = UNSET
    env_key: str | Unset = UNSET
    model_ref: VersionRef | Unset = UNSET
    attempts: int | Unset = UNSET
    generation: int | Unset = UNSET
    source_task_id: str | Unset = UNSET
    source_attempt: int | Unset = UNSET
    trace_id: str | Unset = UNSET
    traceparent: str | Unset = UNSET
    score: float | None | Unset = UNSET
    workspace_uri: str | Unset = UNSET
    agent_sandbox_id: str | Unset = UNSET
    eval_sandbox_id: str | Unset = UNSET
    timings: TaskTimings | Unset = UNSET
    failure: Error | Unset = UNSET
    created_at: datetime.datetime | Unset = UNSET
    updated_at: datetime.datetime | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        task_id = self.task_id

        job_id = self.job_id

        benchmark_ref = self.benchmark_ref.to_dict()

        env_ref = self.env_ref.to_dict()

        harness_ref = self.harness_ref.to_dict()

        reward_ref = self.reward_ref.to_dict()

        model = self.model.to_dict()

        sample_index = self.sample_index

        status = self.status.value

        harness_key = self.harness_key

        env_key = self.env_key

        model_ref: dict[str, Any] | Unset = UNSET
        if not isinstance(self.model_ref, Unset):
            model_ref = self.model_ref.to_dict()

        attempts = self.attempts

        generation = self.generation

        source_task_id = self.source_task_id

        source_attempt = self.source_attempt

        trace_id = self.trace_id

        traceparent = self.traceparent

        score: float | None | Unset
        if isinstance(self.score, Unset):
            score = UNSET
        else:
            score = self.score

        workspace_uri = self.workspace_uri

        agent_sandbox_id = self.agent_sandbox_id

        eval_sandbox_id = self.eval_sandbox_id

        timings: dict[str, Any] | Unset = UNSET
        if not isinstance(self.timings, Unset):
            timings = self.timings.to_dict()

        failure: dict[str, Any] | Unset = UNSET
        if not isinstance(self.failure, Unset):
            failure = self.failure.to_dict()

        created_at: str | Unset = UNSET
        if not isinstance(self.created_at, Unset):
            created_at = self.created_at.isoformat()

        updated_at: str | Unset = UNSET
        if not isinstance(self.updated_at, Unset):
            updated_at = self.updated_at.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "task_id": task_id,
                "job_id": job_id,
                "benchmark_ref": benchmark_ref,
                "env_ref": env_ref,
                "harness_ref": harness_ref,
                "reward_ref": reward_ref,
                "model": model,
                "sample_index": sample_index,
                "status": status,
            }
        )
        if harness_key is not UNSET:
            field_dict["harness_key"] = harness_key
        if env_key is not UNSET:
            field_dict["env_key"] = env_key
        if model_ref is not UNSET:
            field_dict["model_ref"] = model_ref
        if attempts is not UNSET:
            field_dict["attempts"] = attempts
        if generation is not UNSET:
            field_dict["generation"] = generation
        if source_task_id is not UNSET:
            field_dict["source_task_id"] = source_task_id
        if source_attempt is not UNSET:
            field_dict["source_attempt"] = source_attempt
        if trace_id is not UNSET:
            field_dict["trace_id"] = trace_id
        if traceparent is not UNSET:
            field_dict["traceparent"] = traceparent
        if score is not UNSET:
            field_dict["score"] = score
        if workspace_uri is not UNSET:
            field_dict["workspace_uri"] = workspace_uri
        if agent_sandbox_id is not UNSET:
            field_dict["agent_sandbox_id"] = agent_sandbox_id
        if eval_sandbox_id is not UNSET:
            field_dict["eval_sandbox_id"] = eval_sandbox_id
        if timings is not UNSET:
            field_dict["timings"] = timings
        if failure is not UNSET:
            field_dict["failure"] = failure
        if created_at is not UNSET:
            field_dict["created_at"] = created_at
        if updated_at is not UNSET:
            field_dict["updated_at"] = updated_at

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.error import Error
        from ..models.model_spec import ModelSpec
        from ..models.task_timings import TaskTimings
        from ..models.version_ref import VersionRef

        d = dict(src_dict)
        task_id = d.pop("task_id")

        job_id = d.pop("job_id")

        benchmark_ref = VersionRef.from_dict(d.pop("benchmark_ref"))

        env_ref = VersionRef.from_dict(d.pop("env_ref"))

        harness_ref = VersionRef.from_dict(d.pop("harness_ref"))

        reward_ref = VersionRef.from_dict(d.pop("reward_ref"))

        model = ModelSpec.from_dict(d.pop("model"))

        sample_index = d.pop("sample_index")

        status = TaskStatus(d.pop("status"))

        harness_key = d.pop("harness_key", UNSET)

        env_key = d.pop("env_key", UNSET)

        _model_ref = d.pop("model_ref", UNSET)
        model_ref: VersionRef | Unset
        if isinstance(_model_ref, Unset):
            model_ref = UNSET
        else:
            model_ref = VersionRef.from_dict(_model_ref)

        attempts = d.pop("attempts", UNSET)

        generation = d.pop("generation", UNSET)

        source_task_id = d.pop("source_task_id", UNSET)

        source_attempt = d.pop("source_attempt", UNSET)

        trace_id = d.pop("trace_id", UNSET)

        traceparent = d.pop("traceparent", UNSET)

        def _parse_score(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        score = _parse_score(d.pop("score", UNSET))

        workspace_uri = d.pop("workspace_uri", UNSET)

        agent_sandbox_id = d.pop("agent_sandbox_id", UNSET)

        eval_sandbox_id = d.pop("eval_sandbox_id", UNSET)

        _timings = d.pop("timings", UNSET)
        timings: TaskTimings | Unset
        if isinstance(_timings, Unset):
            timings = UNSET
        else:
            timings = TaskTimings.from_dict(_timings)

        _failure = d.pop("failure", UNSET)
        failure: Error | Unset
        if isinstance(_failure, Unset):
            failure = UNSET
        else:
            failure = Error.from_dict(_failure)

        _created_at = d.pop("created_at", UNSET)
        created_at: datetime.datetime | Unset
        if isinstance(_created_at, Unset):
            created_at = UNSET
        else:
            created_at = isoparse(_created_at)

        _updated_at = d.pop("updated_at", UNSET)
        updated_at: datetime.datetime | Unset
        if isinstance(_updated_at, Unset):
            updated_at = UNSET
        else:
            updated_at = isoparse(_updated_at)

        task = cls(
            task_id=task_id,
            job_id=job_id,
            benchmark_ref=benchmark_ref,
            env_ref=env_ref,
            harness_ref=harness_ref,
            reward_ref=reward_ref,
            model=model,
            sample_index=sample_index,
            status=status,
            harness_key=harness_key,
            env_key=env_key,
            model_ref=model_ref,
            attempts=attempts,
            generation=generation,
            source_task_id=source_task_id,
            source_attempt=source_attempt,
            trace_id=trace_id,
            traceparent=traceparent,
            score=score,
            workspace_uri=workspace_uri,
            agent_sandbox_id=agent_sandbox_id,
            eval_sandbox_id=eval_sandbox_id,
            timings=timings,
            failure=failure,
            created_at=created_at,
            updated_at=updated_at,
        )

        task.additional_properties = d
        return task

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
