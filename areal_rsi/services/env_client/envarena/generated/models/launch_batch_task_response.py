from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.benchmark_job import BenchmarkJob


T = TypeVar("T", bound="LaunchBatchTaskResponse")


@_attrs_define
class LaunchBatchTaskResponse:
    """
    Attributes:
        accepted (bool):
        stream_id (str):
        job_id (str):
        task_ids (list[str]):
        job (BenchmarkJob):
    """

    accepted: bool
    stream_id: str
    job_id: str
    task_ids: list[str]
    job: BenchmarkJob
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        accepted = self.accepted

        stream_id = self.stream_id

        job_id = self.job_id

        task_ids = self.task_ids

        job = self.job.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "accepted": accepted,
                "stream_id": stream_id,
                "job_id": job_id,
                "task_ids": task_ids,
                "job": job,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.benchmark_job import BenchmarkJob

        d = dict(src_dict)
        accepted = d.pop("accepted")

        stream_id = d.pop("stream_id")

        job_id = d.pop("job_id")

        task_ids = cast(list[str], d.pop("task_ids"))

        job = BenchmarkJob.from_dict(d.pop("job"))

        launch_batch_task_response = cls(
            accepted=accepted,
            stream_id=stream_id,
            job_id=job_id,
            task_ids=task_ids,
            job=job,
        )

        launch_batch_task_response.additional_properties = d
        return launch_batch_task_response

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
