from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.benchmark_job import BenchmarkJob


T = TypeVar("T", bound="JobAcceptedResponse")


@_attrs_define
class JobAcceptedResponse:
    """
    Attributes:
        job_id (str):
        task_ids (list[str]):
        task_id (str | Unset): Present only when exactly one Task was created.
        job (BenchmarkJob | Unset):
    """

    job_id: str
    task_ids: list[str]
    task_id: str | Unset = UNSET
    job: BenchmarkJob | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        job_id = self.job_id

        task_ids = self.task_ids

        task_id = self.task_id

        job: dict[str, Any] | Unset = UNSET
        if not isinstance(self.job, Unset):
            job = self.job.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "job_id": job_id,
                "task_ids": task_ids,
            }
        )
        if task_id is not UNSET:
            field_dict["task_id"] = task_id
        if job is not UNSET:
            field_dict["job"] = job

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.benchmark_job import BenchmarkJob

        d = dict(src_dict)
        job_id = d.pop("job_id")

        task_ids = cast(list[str], d.pop("task_ids"))

        task_id = d.pop("task_id", UNSET)

        _job = d.pop("job", UNSET)
        job: BenchmarkJob | Unset
        if isinstance(_job, Unset):
            job = UNSET
        else:
            job = BenchmarkJob.from_dict(_job)

        job_accepted_response = cls(
            job_id=job_id,
            task_ids=task_ids,
            task_id=task_id,
            job=job,
        )

        job_accepted_response.additional_properties = d
        return job_accepted_response

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
