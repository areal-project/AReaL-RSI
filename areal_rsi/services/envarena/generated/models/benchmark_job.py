from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.benchmark_job_kind import BenchmarkJobKind
from ..models.job_status import JobStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.benchmark_job_metadata import BenchmarkJobMetadata
    from ..models.job_task_counts import JobTaskCounts
    from ..models.model_spec import ModelSpec
    from ..models.version_ref import VersionRef


T = TypeVar("T", bound="BenchmarkJob")


@_attrs_define
class BenchmarkJob:
    """
    Attributes:
        job_id (str): Readable Job name, normally short-name-YYMMDDHHMM.
        harness_key (str):
        env_keys (list[str]):
        concurrency (int):
        sample_numbers (int):
        status (JobStatus):
        created_at (datetime.datetime):
        kind (BenchmarkJobKind | Unset): BENCHMARK is a normal Agent run; RESCORE is a derived, reward-only run.
            Default: BenchmarkJobKind.BENCHMARK.
        source_job_id (str | Unset): Set only for RESCORE Jobs; identifies the immutable source Benchmark Job.
        source_generation (int | Unset): Execution generation of the source Job whose products are reused.
        effective_reward_ref (VersionRef | Unset):
        benchmark_ref (VersionRef | Unset):
        harness_ref (VersionRef | Unset):
        model_ref (VersionRef | Unset):
        model (ModelSpec | Unset): Compact model runtime config. EnvArena recognizes model_name, parameters, and env;
            unknown fields are preserved but are not used for routing.
        max_retries (int | Unset):
        retry_backoff_seconds (int | Unset):
        config_revision (int | Unset): Optimistic-lock counter for the runtime-mutable settings
            (max_parallel/concurrency, max_retries, retry_backoff_seconds).
            Incremented on every successful PATCH .../config; the PATCH must
            present the revision it read or it is rejected with 409.
        execution_generation (int | Unset): Manual-retry round counter. 1 is the original submission; each
            POST .../retry archives the previous round into the execution
            history and bumps this value.
        avg_score (float | None | Unset):
        task_counts (JobTaskCounts | Unset):
        tags (list[str] | Unset): Selection and search labels attached to this Job.
        metadata (BenchmarkJobMetadata | Unset):
    """

    job_id: str
    harness_key: str
    env_keys: list[str]
    concurrency: int
    sample_numbers: int
    status: JobStatus
    created_at: datetime.datetime
    kind: BenchmarkJobKind | Unset = BenchmarkJobKind.BENCHMARK
    source_job_id: str | Unset = UNSET
    source_generation: int | Unset = UNSET
    effective_reward_ref: VersionRef | Unset = UNSET
    benchmark_ref: VersionRef | Unset = UNSET
    harness_ref: VersionRef | Unset = UNSET
    model_ref: VersionRef | Unset = UNSET
    model: ModelSpec | Unset = UNSET
    max_retries: int | Unset = UNSET
    retry_backoff_seconds: int | Unset = UNSET
    config_revision: int | Unset = UNSET
    execution_generation: int | Unset = UNSET
    avg_score: float | None | Unset = UNSET
    task_counts: JobTaskCounts | Unset = UNSET
    tags: list[str] | Unset = UNSET
    metadata: BenchmarkJobMetadata | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        job_id = self.job_id

        harness_key = self.harness_key

        env_keys = self.env_keys

        concurrency = self.concurrency

        sample_numbers = self.sample_numbers

        status = self.status.value

        created_at = self.created_at.isoformat()

        kind: str | Unset = UNSET
        if not isinstance(self.kind, Unset):
            kind = self.kind.value

        source_job_id = self.source_job_id

        source_generation = self.source_generation

        effective_reward_ref: dict[str, Any] | Unset = UNSET
        if not isinstance(self.effective_reward_ref, Unset):
            effective_reward_ref = self.effective_reward_ref.to_dict()

        benchmark_ref: dict[str, Any] | Unset = UNSET
        if not isinstance(self.benchmark_ref, Unset):
            benchmark_ref = self.benchmark_ref.to_dict()

        harness_ref: dict[str, Any] | Unset = UNSET
        if not isinstance(self.harness_ref, Unset):
            harness_ref = self.harness_ref.to_dict()

        model_ref: dict[str, Any] | Unset = UNSET
        if not isinstance(self.model_ref, Unset):
            model_ref = self.model_ref.to_dict()

        model: dict[str, Any] | Unset = UNSET
        if not isinstance(self.model, Unset):
            model = self.model.to_dict()

        max_retries = self.max_retries

        retry_backoff_seconds = self.retry_backoff_seconds

        config_revision = self.config_revision

        execution_generation = self.execution_generation

        avg_score: float | None | Unset
        if isinstance(self.avg_score, Unset):
            avg_score = UNSET
        else:
            avg_score = self.avg_score

        task_counts: dict[str, Any] | Unset = UNSET
        if not isinstance(self.task_counts, Unset):
            task_counts = self.task_counts.to_dict()

        tags: list[str] | Unset = UNSET
        if not isinstance(self.tags, Unset):
            tags = self.tags

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "job_id": job_id,
                "harness_key": harness_key,
                "env_keys": env_keys,
                "concurrency": concurrency,
                "sample_numbers": sample_numbers,
                "status": status,
                "created_at": created_at,
            }
        )
        if kind is not UNSET:
            field_dict["kind"] = kind
        if source_job_id is not UNSET:
            field_dict["source_job_id"] = source_job_id
        if source_generation is not UNSET:
            field_dict["source_generation"] = source_generation
        if effective_reward_ref is not UNSET:
            field_dict["effective_reward_ref"] = effective_reward_ref
        if benchmark_ref is not UNSET:
            field_dict["benchmark_ref"] = benchmark_ref
        if harness_ref is not UNSET:
            field_dict["harness_ref"] = harness_ref
        if model_ref is not UNSET:
            field_dict["model_ref"] = model_ref
        if model is not UNSET:
            field_dict["model"] = model
        if max_retries is not UNSET:
            field_dict["max_retries"] = max_retries
        if retry_backoff_seconds is not UNSET:
            field_dict["retry_backoff_seconds"] = retry_backoff_seconds
        if config_revision is not UNSET:
            field_dict["config_revision"] = config_revision
        if execution_generation is not UNSET:
            field_dict["execution_generation"] = execution_generation
        if avg_score is not UNSET:
            field_dict["avg_score"] = avg_score
        if task_counts is not UNSET:
            field_dict["task_counts"] = task_counts
        if tags is not UNSET:
            field_dict["tags"] = tags
        if metadata is not UNSET:
            field_dict["metadata"] = metadata

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.benchmark_job_metadata import BenchmarkJobMetadata
        from ..models.job_task_counts import JobTaskCounts
        from ..models.model_spec import ModelSpec
        from ..models.version_ref import VersionRef

        d = dict(src_dict)
        job_id = d.pop("job_id")

        harness_key = d.pop("harness_key")

        env_keys = cast(list[str], d.pop("env_keys"))

        concurrency = d.pop("concurrency")

        sample_numbers = d.pop("sample_numbers")

        status = JobStatus(d.pop("status"))

        created_at = isoparse(d.pop("created_at"))

        _kind = d.pop("kind", UNSET)
        kind: BenchmarkJobKind | Unset
        if isinstance(_kind, Unset):
            kind = UNSET
        else:
            kind = BenchmarkJobKind(_kind)

        source_job_id = d.pop("source_job_id", UNSET)

        source_generation = d.pop("source_generation", UNSET)

        _effective_reward_ref = d.pop("effective_reward_ref", UNSET)
        effective_reward_ref: VersionRef | Unset
        if isinstance(_effective_reward_ref, Unset):
            effective_reward_ref = UNSET
        else:
            effective_reward_ref = VersionRef.from_dict(_effective_reward_ref)

        _benchmark_ref = d.pop("benchmark_ref", UNSET)
        benchmark_ref: VersionRef | Unset
        if isinstance(_benchmark_ref, Unset):
            benchmark_ref = UNSET
        else:
            benchmark_ref = VersionRef.from_dict(_benchmark_ref)

        _harness_ref = d.pop("harness_ref", UNSET)
        harness_ref: VersionRef | Unset
        if isinstance(_harness_ref, Unset):
            harness_ref = UNSET
        else:
            harness_ref = VersionRef.from_dict(_harness_ref)

        _model_ref = d.pop("model_ref", UNSET)
        model_ref: VersionRef | Unset
        if isinstance(_model_ref, Unset):
            model_ref = UNSET
        else:
            model_ref = VersionRef.from_dict(_model_ref)

        _model = d.pop("model", UNSET)
        model: ModelSpec | Unset
        if isinstance(_model, Unset):
            model = UNSET
        else:
            model = ModelSpec.from_dict(_model)

        max_retries = d.pop("max_retries", UNSET)

        retry_backoff_seconds = d.pop("retry_backoff_seconds", UNSET)

        config_revision = d.pop("config_revision", UNSET)

        execution_generation = d.pop("execution_generation", UNSET)

        def _parse_avg_score(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        avg_score = _parse_avg_score(d.pop("avg_score", UNSET))

        _task_counts = d.pop("task_counts", UNSET)
        task_counts: JobTaskCounts | Unset
        if isinstance(_task_counts, Unset):
            task_counts = UNSET
        else:
            task_counts = JobTaskCounts.from_dict(_task_counts)

        tags = cast(list[str], d.pop("tags", UNSET))

        _metadata = d.pop("metadata", UNSET)
        metadata: BenchmarkJobMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = BenchmarkJobMetadata.from_dict(_metadata)

        benchmark_job = cls(
            job_id=job_id,
            harness_key=harness_key,
            env_keys=env_keys,
            concurrency=concurrency,
            sample_numbers=sample_numbers,
            status=status,
            created_at=created_at,
            kind=kind,
            source_job_id=source_job_id,
            source_generation=source_generation,
            effective_reward_ref=effective_reward_ref,
            benchmark_ref=benchmark_ref,
            harness_ref=harness_ref,
            model_ref=model_ref,
            model=model,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
            config_revision=config_revision,
            execution_generation=execution_generation,
            avg_score=avg_score,
            task_counts=task_counts,
            tags=tags,
            metadata=metadata,
        )

        benchmark_job.additional_properties = d
        return benchmark_job

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
