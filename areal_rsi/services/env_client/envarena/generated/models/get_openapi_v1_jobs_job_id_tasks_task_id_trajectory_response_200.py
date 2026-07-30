from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.get_openapi_v1_jobs_job_id_tasks_task_id_trajectory_response_200_events_item import (
        GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryResponse200EventsItem,
    )


T = TypeVar("T", bound="GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryResponse200")


@_attrs_define
class GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryResponse200:
    """
    Attributes:
        task_id (str | Unset):
        attempt (int | Unset):
        event_count (int | Unset):
        events (list[GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryResponse200EventsItem] | Unset):
    """

    task_id: str | Unset = UNSET
    attempt: int | Unset = UNSET
    event_count: int | Unset = UNSET
    events: (
        list[GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryResponse200EventsItem] | Unset
    ) = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        task_id = self.task_id

        attempt = self.attempt

        event_count = self.event_count

        events: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.events, Unset):
            events = []
            for events_item_data in self.events:
                events_item = events_item_data.to_dict()
                events.append(events_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if task_id is not UNSET:
            field_dict["task_id"] = task_id
        if attempt is not UNSET:
            field_dict["attempt"] = attempt
        if event_count is not UNSET:
            field_dict["event_count"] = event_count
        if events is not UNSET:
            field_dict["events"] = events

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.get_openapi_v1_jobs_job_id_tasks_task_id_trajectory_response_200_events_item import (
            GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryResponse200EventsItem,
        )

        d = dict(src_dict)
        task_id = d.pop("task_id", UNSET)

        attempt = d.pop("attempt", UNSET)

        event_count = d.pop("event_count", UNSET)

        _events = d.pop("events", UNSET)
        events: (
            list[GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryResponse200EventsItem]
            | Unset
        ) = UNSET
        if _events is not UNSET:
            events = []
            for events_item_data in _events:
                events_item = GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryResponse200EventsItem.from_dict(
                    events_item_data
                )

                events.append(events_item)

        get_openapi_v1_jobs_job_id_tasks_task_id_trajectory_response_200 = cls(
            task_id=task_id,
            attempt=attempt,
            event_count=event_count,
            events=events,
        )

        get_openapi_v1_jobs_job_id_tasks_task_id_trajectory_response_200.additional_properties = d
        return get_openapi_v1_jobs_job_id_tasks_task_id_trajectory_response_200

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
