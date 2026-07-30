from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.benchmark_job import BenchmarkJob
    from ..models.job_result import JobResult
    from ..models.task import Task


T = TypeVar("T", bound="GetOpenapiV1JobsJobIdResponse200")


@_attrs_define
class GetOpenapiV1JobsJobIdResponse200:
    """
    Attributes:
        job (BenchmarkJob | Unset):
        tasks (list[Task] | Unset):
        result (JobResult | Unset):
    """

    job: BenchmarkJob | Unset = UNSET
    tasks: list[Task] | Unset = UNSET
    result: JobResult | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        job: dict[str, Any] | Unset = UNSET
        if not isinstance(self.job, Unset):
            job = self.job.to_dict()

        tasks: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.tasks, Unset):
            tasks = []
            for tasks_item_data in self.tasks:
                tasks_item = tasks_item_data.to_dict()
                tasks.append(tasks_item)

        result: dict[str, Any] | Unset = UNSET
        if not isinstance(self.result, Unset):
            result = self.result.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if job is not UNSET:
            field_dict["job"] = job
        if tasks is not UNSET:
            field_dict["tasks"] = tasks
        if result is not UNSET:
            field_dict["result"] = result

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.benchmark_job import BenchmarkJob
        from ..models.job_result import JobResult
        from ..models.task import Task

        d = dict(src_dict)
        _job = d.pop("job", UNSET)
        job: BenchmarkJob | Unset
        if isinstance(_job, Unset):
            job = UNSET
        else:
            job = BenchmarkJob.from_dict(_job)

        _tasks = d.pop("tasks", UNSET)
        tasks: list[Task] | Unset = UNSET
        if _tasks is not UNSET:
            tasks = []
            for tasks_item_data in _tasks:
                tasks_item = Task.from_dict(tasks_item_data)

                tasks.append(tasks_item)

        _result = d.pop("result", UNSET)
        result: JobResult | Unset
        if isinstance(_result, Unset):
            result = UNSET
        else:
            result = JobResult.from_dict(_result)

        get_openapi_v1_jobs_job_id_response_200 = cls(
            job=job,
            tasks=tasks,
            result=result,
        )

        get_openapi_v1_jobs_job_id_response_200.additional_properties = d
        return get_openapi_v1_jobs_job_id_response_200

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
