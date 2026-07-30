from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.benchmark_job import BenchmarkJob


T = TypeVar("T", bound="StartJobResponse")


@_attrs_define
class StartJobResponse:
    """
    Attributes:
        job (BenchmarkJob):
        reset_tasks (int): Number of non-DONE tasks reset to PENDING for the re-run.
    """

    job: BenchmarkJob
    reset_tasks: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        job = self.job.to_dict()

        reset_tasks = self.reset_tasks

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "job": job,
                "reset_tasks": reset_tasks,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.benchmark_job import BenchmarkJob

        d = dict(src_dict)
        job = BenchmarkJob.from_dict(d.pop("job"))

        reset_tasks = d.pop("reset_tasks")

        start_job_response = cls(
            job=job,
            reset_tasks=reset_tasks,
        )

        start_job_response.additional_properties = d
        return start_job_response

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
