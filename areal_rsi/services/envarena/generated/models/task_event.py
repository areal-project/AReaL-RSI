from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

T = TypeVar("T", bound="TaskEvent")


@_attrs_define
class TaskEvent:
    """
    Attributes:
        id (int):
        task_id (str):
        attempt (int):
        phase (str):
        status (str):
        message (str):
        timestamp (datetime.datetime):
        data_ref (str | Unset):
    """

    id: int
    task_id: str
    attempt: int
    phase: str
    status: str
    message: str
    timestamp: datetime.datetime
    data_ref: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        task_id = self.task_id

        attempt = self.attempt

        phase = self.phase

        status = self.status

        message = self.message

        timestamp = self.timestamp.isoformat()

        data_ref = self.data_ref

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "task_id": task_id,
                "attempt": attempt,
                "phase": phase,
                "status": status,
                "message": message,
                "timestamp": timestamp,
            }
        )
        if data_ref is not UNSET:
            field_dict["data_ref"] = data_ref

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        id = d.pop("id")

        task_id = d.pop("task_id")

        attempt = d.pop("attempt")

        phase = d.pop("phase")

        status = d.pop("status")

        message = d.pop("message")

        timestamp = isoparse(d.pop("timestamp"))

        data_ref = d.pop("data_ref", UNSET)

        task_event = cls(
            id=id,
            task_id=task_id,
            attempt=attempt,
            phase=phase,
            status=status,
            message=message,
            timestamp=timestamp,
            data_ref=data_ref,
        )

        task_event.additional_properties = d
        return task_event

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
