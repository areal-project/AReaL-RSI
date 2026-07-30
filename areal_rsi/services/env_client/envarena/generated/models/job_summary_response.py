from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.benchmark_job import BenchmarkJob
    from ..models.job_result import JobResult
    from ..models.job_sample_summary import JobSampleSummary
    from ..models.job_task_counts import JobTaskCounts
    from ..models.version_ref import VersionRef


T = TypeVar("T", bound="JobSummaryResponse")


@_attrs_define
class JobSummaryResponse:
    """
    Attributes:
        job (BenchmarkJob):
        task_counts (JobTaskCounts):
        env_count (int):
        result (JobResult | Unset):
        reward_ref (VersionRef | Unset):
        sample_summary (list[JobSampleSummary] | Unset):
    """

    job: BenchmarkJob
    task_counts: JobTaskCounts
    env_count: int
    result: JobResult | Unset = UNSET
    reward_ref: VersionRef | Unset = UNSET
    sample_summary: list[JobSampleSummary] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        job = self.job.to_dict()

        task_counts = self.task_counts.to_dict()

        env_count = self.env_count

        result: dict[str, Any] | Unset = UNSET
        if not isinstance(self.result, Unset):
            result = self.result.to_dict()

        reward_ref: dict[str, Any] | Unset = UNSET
        if not isinstance(self.reward_ref, Unset):
            reward_ref = self.reward_ref.to_dict()

        sample_summary: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.sample_summary, Unset):
            sample_summary = []
            for sample_summary_item_data in self.sample_summary:
                sample_summary_item = sample_summary_item_data.to_dict()
                sample_summary.append(sample_summary_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "job": job,
                "task_counts": task_counts,
                "env_count": env_count,
            }
        )
        if result is not UNSET:
            field_dict["result"] = result
        if reward_ref is not UNSET:
            field_dict["reward_ref"] = reward_ref
        if sample_summary is not UNSET:
            field_dict["sample_summary"] = sample_summary

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.benchmark_job import BenchmarkJob
        from ..models.job_result import JobResult
        from ..models.job_sample_summary import JobSampleSummary
        from ..models.job_task_counts import JobTaskCounts
        from ..models.version_ref import VersionRef

        d = dict(src_dict)
        job = BenchmarkJob.from_dict(d.pop("job"))

        task_counts = JobTaskCounts.from_dict(d.pop("task_counts"))

        env_count = d.pop("env_count")

        _result = d.pop("result", UNSET)
        result: JobResult | Unset
        if isinstance(_result, Unset):
            result = UNSET
        else:
            result = JobResult.from_dict(_result)

        _reward_ref = d.pop("reward_ref", UNSET)
        reward_ref: VersionRef | Unset
        if isinstance(_reward_ref, Unset):
            reward_ref = UNSET
        else:
            reward_ref = VersionRef.from_dict(_reward_ref)

        _sample_summary = d.pop("sample_summary", UNSET)
        sample_summary: list[JobSampleSummary] | Unset = UNSET
        if _sample_summary is not UNSET:
            sample_summary = []
            for sample_summary_item_data in _sample_summary:
                sample_summary_item = JobSampleSummary.from_dict(
                    sample_summary_item_data
                )

                sample_summary.append(sample_summary_item)

        job_summary_response = cls(
            job=job,
            task_counts=task_counts,
            env_count=env_count,
            result=result,
            reward_ref=reward_ref,
            sample_summary=sample_summary,
        )

        job_summary_response.additional_properties = d
        return job_summary_response

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
