from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.benchmark_job import BenchmarkJob


T = TypeVar("T", bound="RetryJobResponse")


@_attrs_define
class RetryJobResponse:
    """
    Attributes:
        job (BenchmarkJob):
        generation (int): The execution generation now running.
        reset_task_ids (list[str]):
        reused (bool): True when the idempotency key matched an earlier retry and nothing was re-run.
    """

    job: BenchmarkJob
    generation: int
    reset_task_ids: list[str]
    reused: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        job = self.job.to_dict()

        generation = self.generation

        reset_task_ids = self.reset_task_ids

        reused = self.reused

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "job": job,
                "generation": generation,
                "reset_task_ids": reset_task_ids,
                "reused": reused,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.benchmark_job import BenchmarkJob

        d = dict(src_dict)
        job = BenchmarkJob.from_dict(d.pop("job"))

        generation = d.pop("generation")

        reset_task_ids = cast(list[str], d.pop("reset_task_ids"))

        reused = d.pop("reused")

        retry_job_response = cls(
            job=job,
            generation=generation,
            reset_task_ids=reset_task_ids,
            reused=reused,
        )

        retry_job_response.additional_properties = d
        return retry_job_response

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
