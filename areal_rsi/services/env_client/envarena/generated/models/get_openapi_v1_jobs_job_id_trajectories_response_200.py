from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.get_openapi_v1_jobs_job_id_trajectories_response_200_tasks_item import (
        GetOpenapiV1JobsJobIdTrajectoriesResponse200TasksItem,
    )


T = TypeVar("T", bound="GetOpenapiV1JobsJobIdTrajectoriesResponse200")


@_attrs_define
class GetOpenapiV1JobsJobIdTrajectoriesResponse200:
    """
    Attributes:
        job_id (str | Unset):
        tasks (list[GetOpenapiV1JobsJobIdTrajectoriesResponse200TasksItem] | Unset):
    """

    job_id: str | Unset = UNSET
    tasks: list[GetOpenapiV1JobsJobIdTrajectoriesResponse200TasksItem] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        job_id = self.job_id

        tasks: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.tasks, Unset):
            tasks = []
            for tasks_item_data in self.tasks:
                tasks_item = tasks_item_data.to_dict()
                tasks.append(tasks_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if job_id is not UNSET:
            field_dict["job_id"] = job_id
        if tasks is not UNSET:
            field_dict["tasks"] = tasks

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.get_openapi_v1_jobs_job_id_trajectories_response_200_tasks_item import (
            GetOpenapiV1JobsJobIdTrajectoriesResponse200TasksItem,
        )

        d = dict(src_dict)
        job_id = d.pop("job_id", UNSET)

        _tasks = d.pop("tasks", UNSET)
        tasks: list[GetOpenapiV1JobsJobIdTrajectoriesResponse200TasksItem] | Unset = (
            UNSET
        )
        if _tasks is not UNSET:
            tasks = []
            for tasks_item_data in _tasks:
                tasks_item = (
                    GetOpenapiV1JobsJobIdTrajectoriesResponse200TasksItem.from_dict(
                        tasks_item_data
                    )
                )

                tasks.append(tasks_item)

        get_openapi_v1_jobs_job_id_trajectories_response_200 = cls(
            job_id=job_id,
            tasks=tasks,
        )

        get_openapi_v1_jobs_job_id_trajectories_response_200.additional_properties = d
        return get_openapi_v1_jobs_job_id_trajectories_response_200

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
