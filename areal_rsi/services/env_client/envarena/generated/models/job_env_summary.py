from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.job_env_summary_status import JobEnvSummaryStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.version_ref import VersionRef


T = TypeVar("T", bound="JobEnvSummary")


@_attrs_define
class JobEnvSummary:
    """
    Attributes:
        env (str):
        env_key (str): Stable Env grouping key, normally Env@version when env_ref is present.
        env_label (str):
        status (JobEnvSummaryStatus):
        weight (float):
        count (int):
        total (int):
        done_count (int):
        failed_count (int):
        running_count (int):
        pending_count (int):
        completed_count (int):
        task_count (int):
        visible_task_count (int):
        progress (int):
        total_duration_ms (int):
        avg_duration_ms (float):
        env_ref (VersionRef | Unset):
        aggregate_score (float | None | Unset):
        raw_mean (float | None | Unset):
        best_reward (float | None | Unset):
        method (str | Unset):
        pass_threshold (float | Unset):
        passed (bool | Unset):
    """

    env: str
    env_key: str
    env_label: str
    status: JobEnvSummaryStatus
    weight: float
    count: int
    total: int
    done_count: int
    failed_count: int
    running_count: int
    pending_count: int
    completed_count: int
    task_count: int
    visible_task_count: int
    progress: int
    total_duration_ms: int
    avg_duration_ms: float
    env_ref: VersionRef | Unset = UNSET
    aggregate_score: float | None | Unset = UNSET
    raw_mean: float | None | Unset = UNSET
    best_reward: float | None | Unset = UNSET
    method: str | Unset = UNSET
    pass_threshold: float | Unset = UNSET
    passed: bool | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        env = self.env

        env_key = self.env_key

        env_label = self.env_label

        status = self.status.value

        weight = self.weight

        count = self.count

        total = self.total

        done_count = self.done_count

        failed_count = self.failed_count

        running_count = self.running_count

        pending_count = self.pending_count

        completed_count = self.completed_count

        task_count = self.task_count

        visible_task_count = self.visible_task_count

        progress = self.progress

        total_duration_ms = self.total_duration_ms

        avg_duration_ms = self.avg_duration_ms

        env_ref: dict[str, Any] | Unset = UNSET
        if not isinstance(self.env_ref, Unset):
            env_ref = self.env_ref.to_dict()

        aggregate_score: float | None | Unset
        if isinstance(self.aggregate_score, Unset):
            aggregate_score = UNSET
        else:
            aggregate_score = self.aggregate_score

        raw_mean: float | None | Unset
        if isinstance(self.raw_mean, Unset):
            raw_mean = UNSET
        else:
            raw_mean = self.raw_mean

        best_reward: float | None | Unset
        if isinstance(self.best_reward, Unset):
            best_reward = UNSET
        else:
            best_reward = self.best_reward

        method = self.method

        pass_threshold = self.pass_threshold

        passed = self.passed

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "env": env,
                "env_key": env_key,
                "env_label": env_label,
                "status": status,
                "weight": weight,
                "count": count,
                "total": total,
                "done_count": done_count,
                "failed_count": failed_count,
                "running_count": running_count,
                "pending_count": pending_count,
                "completed_count": completed_count,
                "task_count": task_count,
                "visible_task_count": visible_task_count,
                "progress": progress,
                "total_duration_ms": total_duration_ms,
                "avg_duration_ms": avg_duration_ms,
            }
        )
        if env_ref is not UNSET:
            field_dict["env_ref"] = env_ref
        if aggregate_score is not UNSET:
            field_dict["aggregate_score"] = aggregate_score
        if raw_mean is not UNSET:
            field_dict["raw_mean"] = raw_mean
        if best_reward is not UNSET:
            field_dict["best_reward"] = best_reward
        if method is not UNSET:
            field_dict["method"] = method
        if pass_threshold is not UNSET:
            field_dict["pass_threshold"] = pass_threshold
        if passed is not UNSET:
            field_dict["passed"] = passed

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.version_ref import VersionRef

        d = dict(src_dict)
        env = d.pop("env")

        env_key = d.pop("env_key")

        env_label = d.pop("env_label")

        status = JobEnvSummaryStatus(d.pop("status"))

        weight = d.pop("weight")

        count = d.pop("count")

        total = d.pop("total")

        done_count = d.pop("done_count")

        failed_count = d.pop("failed_count")

        running_count = d.pop("running_count")

        pending_count = d.pop("pending_count")

        completed_count = d.pop("completed_count")

        task_count = d.pop("task_count")

        visible_task_count = d.pop("visible_task_count")

        progress = d.pop("progress")

        total_duration_ms = d.pop("total_duration_ms")

        avg_duration_ms = d.pop("avg_duration_ms")

        _env_ref = d.pop("env_ref", UNSET)
        env_ref: VersionRef | Unset
        if isinstance(_env_ref, Unset):
            env_ref = UNSET
        else:
            env_ref = VersionRef.from_dict(_env_ref)

        def _parse_aggregate_score(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        aggregate_score = _parse_aggregate_score(d.pop("aggregate_score", UNSET))

        def _parse_raw_mean(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        raw_mean = _parse_raw_mean(d.pop("raw_mean", UNSET))

        def _parse_best_reward(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        best_reward = _parse_best_reward(d.pop("best_reward", UNSET))

        method = d.pop("method", UNSET)

        pass_threshold = d.pop("pass_threshold", UNSET)

        passed = d.pop("passed", UNSET)

        job_env_summary = cls(
            env=env,
            env_key=env_key,
            env_label=env_label,
            status=status,
            weight=weight,
            count=count,
            total=total,
            done_count=done_count,
            failed_count=failed_count,
            running_count=running_count,
            pending_count=pending_count,
            completed_count=completed_count,
            task_count=task_count,
            visible_task_count=visible_task_count,
            progress=progress,
            total_duration_ms=total_duration_ms,
            avg_duration_ms=avg_duration_ms,
            env_ref=env_ref,
            aggregate_score=aggregate_score,
            raw_mean=raw_mean,
            best_reward=best_reward,
            method=method,
            pass_threshold=pass_threshold,
            passed=passed,
        )

        job_env_summary.additional_properties = d
        return job_env_summary

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
