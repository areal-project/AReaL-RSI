from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.get_openapi_v1_jobs_job_id_tasks_task_id_logs_kind_response_200_kind import (
    GetOpenapiV1JobsJobIdTasksTaskIdLogsKindResponse200Kind,
)
from ..types import UNSET, Unset

T = TypeVar("T", bound="GetOpenapiV1JobsJobIdTasksTaskIdLogsKindResponse200")


@_attrs_define
class GetOpenapiV1JobsJobIdTasksTaskIdLogsKindResponse200:
    """
    Attributes:
        task_id (str):
        attempt (int):
        kind (GetOpenapiV1JobsJobIdTasksTaskIdLogsKindResponse200Kind):
        source_kind (str): Object that actually served the page (harness, runner, reward, reward_shared).
        offset (int): Byte offset of the first returned byte after UTF-8 alignment.
        limit (int):
        total_bytes (int):
        has_more (bool):
        content (str):
        next_offset (int | Unset): Offset of the next unread byte. Omitted on the final page.
    """

    task_id: str
    attempt: int
    kind: GetOpenapiV1JobsJobIdTasksTaskIdLogsKindResponse200Kind
    source_kind: str
    offset: int
    limit: int
    total_bytes: int
    has_more: bool
    content: str
    next_offset: int | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        task_id = self.task_id

        attempt = self.attempt

        kind = self.kind.value

        source_kind = self.source_kind

        offset = self.offset

        limit = self.limit

        total_bytes = self.total_bytes

        has_more = self.has_more

        content = self.content

        next_offset = self.next_offset

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "task_id": task_id,
                "attempt": attempt,
                "kind": kind,
                "source_kind": source_kind,
                "offset": offset,
                "limit": limit,
                "total_bytes": total_bytes,
                "has_more": has_more,
                "content": content,
            }
        )
        if next_offset is not UNSET:
            field_dict["next_offset"] = next_offset

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        task_id = d.pop("task_id")

        attempt = d.pop("attempt")

        kind = GetOpenapiV1JobsJobIdTasksTaskIdLogsKindResponse200Kind(d.pop("kind"))

        source_kind = d.pop("source_kind")

        offset = d.pop("offset")

        limit = d.pop("limit")

        total_bytes = d.pop("total_bytes")

        has_more = d.pop("has_more")

        content = d.pop("content")

        next_offset = d.pop("next_offset", UNSET)

        get_openapi_v1_jobs_job_id_tasks_task_id_logs_kind_response_200 = cls(
            task_id=task_id,
            attempt=attempt,
            kind=kind,
            source_kind=source_kind,
            offset=offset,
            limit=limit,
            total_bytes=total_bytes,
            has_more=has_more,
            content=content,
            next_offset=next_offset,
        )

        get_openapi_v1_jobs_job_id_tasks_task_id_logs_kind_response_200.additional_properties = d
        return get_openapi_v1_jobs_job_id_tasks_task_id_logs_kind_response_200

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
