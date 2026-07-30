from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.get_openapi_v1_jobs_job_id_trajectories_response_200_tasks_item_events_item import (
        GetOpenapiV1JobsJobIdTrajectoriesResponse200TasksItemEventsItem,
    )


T = TypeVar("T", bound="GetOpenapiV1JobsJobIdTrajectoriesResponse200TasksItem")


@_attrs_define
class GetOpenapiV1JobsJobIdTrajectoriesResponse200TasksItem:
    """
    Attributes:
        task_id (str | Unset):
        env_key (str | Unset):
        sample_index (int | Unset):
        status (str | Unset):
        attempts (int | Unset):
        latest_attempt (int | Unset):
        trajectory_uri (str | Unset):
        event_count (int | Unset):
        events (list[GetOpenapiV1JobsJobIdTrajectoriesResponse200TasksItemEventsItem] | Unset):
    """

    task_id: str | Unset = UNSET
    env_key: str | Unset = UNSET
    sample_index: int | Unset = UNSET
    status: str | Unset = UNSET
    attempts: int | Unset = UNSET
    latest_attempt: int | Unset = UNSET
    trajectory_uri: str | Unset = UNSET
    event_count: int | Unset = UNSET
    events: (
        list[GetOpenapiV1JobsJobIdTrajectoriesResponse200TasksItemEventsItem] | Unset
    ) = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        task_id = self.task_id

        env_key = self.env_key

        sample_index = self.sample_index

        status = self.status

        attempts = self.attempts

        latest_attempt = self.latest_attempt

        trajectory_uri = self.trajectory_uri

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
        if env_key is not UNSET:
            field_dict["env_key"] = env_key
        if sample_index is not UNSET:
            field_dict["sample_index"] = sample_index
        if status is not UNSET:
            field_dict["status"] = status
        if attempts is not UNSET:
            field_dict["attempts"] = attempts
        if latest_attempt is not UNSET:
            field_dict["latest_attempt"] = latest_attempt
        if trajectory_uri is not UNSET:
            field_dict["trajectory_uri"] = trajectory_uri
        if event_count is not UNSET:
            field_dict["event_count"] = event_count
        if events is not UNSET:
            field_dict["events"] = events

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.get_openapi_v1_jobs_job_id_trajectories_response_200_tasks_item_events_item import (
            GetOpenapiV1JobsJobIdTrajectoriesResponse200TasksItemEventsItem,
        )

        d = dict(src_dict)
        task_id = d.pop("task_id", UNSET)

        env_key = d.pop("env_key", UNSET)

        sample_index = d.pop("sample_index", UNSET)

        status = d.pop("status", UNSET)

        attempts = d.pop("attempts", UNSET)

        latest_attempt = d.pop("latest_attempt", UNSET)

        trajectory_uri = d.pop("trajectory_uri", UNSET)

        event_count = d.pop("event_count", UNSET)

        _events = d.pop("events", UNSET)
        events: (
            list[GetOpenapiV1JobsJobIdTrajectoriesResponse200TasksItemEventsItem]
            | Unset
        ) = UNSET
        if _events is not UNSET:
            events = []
            for events_item_data in _events:
                events_item = GetOpenapiV1JobsJobIdTrajectoriesResponse200TasksItemEventsItem.from_dict(
                    events_item_data
                )

                events.append(events_item)

        get_openapi_v1_jobs_job_id_trajectories_response_200_tasks_item = cls(
            task_id=task_id,
            env_key=env_key,
            sample_index=sample_index,
            status=status,
            attempts=attempts,
            latest_attempt=latest_attempt,
            trajectory_uri=trajectory_uri,
            event_count=event_count,
            events=events,
        )

        get_openapi_v1_jobs_job_id_trajectories_response_200_tasks_item.additional_properties = d
        return get_openapi_v1_jobs_job_id_trajectories_response_200_tasks_item

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
