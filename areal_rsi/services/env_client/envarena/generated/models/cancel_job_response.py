from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.benchmark_job import BenchmarkJob
    from ..models.cancel_job_response_skipped_sandboxes import (
        CancelJobResponseSkippedSandboxes,
    )
    from ..models.task import Task


T = TypeVar("T", bound="CancelJobResponse")


@_attrs_define
class CancelJobResponse:
    """
    Attributes:
        job (BenchmarkJob):
        tasks (list[Task]):
        cancelled_tasks (int):
        destroyed_sandboxes (list[str] | Unset):
        skipped_sandboxes (CancelJobResponseSkippedSandboxes | Unset):
    """

    job: BenchmarkJob
    tasks: list[Task]
    cancelled_tasks: int
    destroyed_sandboxes: list[str] | Unset = UNSET
    skipped_sandboxes: CancelJobResponseSkippedSandboxes | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        job = self.job.to_dict()

        tasks = []
        for tasks_item_data in self.tasks:
            tasks_item = tasks_item_data.to_dict()
            tasks.append(tasks_item)

        cancelled_tasks = self.cancelled_tasks

        destroyed_sandboxes: list[str] | Unset = UNSET
        if not isinstance(self.destroyed_sandboxes, Unset):
            destroyed_sandboxes = self.destroyed_sandboxes

        skipped_sandboxes: dict[str, Any] | Unset = UNSET
        if not isinstance(self.skipped_sandboxes, Unset):
            skipped_sandboxes = self.skipped_sandboxes.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "job": job,
                "tasks": tasks,
                "cancelled_tasks": cancelled_tasks,
            }
        )
        if destroyed_sandboxes is not UNSET:
            field_dict["destroyed_sandboxes"] = destroyed_sandboxes
        if skipped_sandboxes is not UNSET:
            field_dict["skipped_sandboxes"] = skipped_sandboxes

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.benchmark_job import BenchmarkJob
        from ..models.cancel_job_response_skipped_sandboxes import (
            CancelJobResponseSkippedSandboxes,
        )
        from ..models.task import Task

        d = dict(src_dict)
        job = BenchmarkJob.from_dict(d.pop("job"))

        tasks = []
        _tasks = d.pop("tasks")
        for tasks_item_data in _tasks:
            tasks_item = Task.from_dict(tasks_item_data)

            tasks.append(tasks_item)

        cancelled_tasks = d.pop("cancelled_tasks")

        destroyed_sandboxes = cast(list[str], d.pop("destroyed_sandboxes", UNSET))

        _skipped_sandboxes = d.pop("skipped_sandboxes", UNSET)
        skipped_sandboxes: CancelJobResponseSkippedSandboxes | Unset
        if isinstance(_skipped_sandboxes, Unset):
            skipped_sandboxes = UNSET
        else:
            skipped_sandboxes = CancelJobResponseSkippedSandboxes.from_dict(
                _skipped_sandboxes
            )

        cancel_job_response = cls(
            job=job,
            tasks=tasks,
            cancelled_tasks=cancelled_tasks,
            destroyed_sandboxes=destroyed_sandboxes,
            skipped_sandboxes=skipped_sandboxes,
        )

        cancel_job_response.additional_properties = d
        return cancel_job_response

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
