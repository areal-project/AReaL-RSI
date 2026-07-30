from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.stream_event_after import StreamEventAfter
    from ..models.stream_event_before import StreamEventBefore


T = TypeVar("T", bound="StreamEvent")


@_attrs_define
class StreamEvent:
    """
    Attributes:
        event_id (int):
        stream_id (str):
        type_ (str):
        created_at (datetime.datetime):
        actor (str | Unset):
        message (str | Unset):
        before (StreamEventBefore | Unset):
        after (StreamEventAfter | Unset):
    """

    event_id: int
    stream_id: str
    type_: str
    created_at: datetime.datetime
    actor: str | Unset = UNSET
    message: str | Unset = UNSET
    before: StreamEventBefore | Unset = UNSET
    after: StreamEventAfter | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        event_id = self.event_id

        stream_id = self.stream_id

        type_ = self.type_

        created_at = self.created_at.isoformat()

        actor = self.actor

        message = self.message

        before: dict[str, Any] | Unset = UNSET
        if not isinstance(self.before, Unset):
            before = self.before.to_dict()

        after: dict[str, Any] | Unset = UNSET
        if not isinstance(self.after, Unset):
            after = self.after.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "event_id": event_id,
                "stream_id": stream_id,
                "type": type_,
                "created_at": created_at,
            }
        )
        if actor is not UNSET:
            field_dict["actor"] = actor
        if message is not UNSET:
            field_dict["message"] = message
        if before is not UNSET:
            field_dict["before"] = before
        if after is not UNSET:
            field_dict["after"] = after

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.stream_event_after import StreamEventAfter
        from ..models.stream_event_before import StreamEventBefore

        d = dict(src_dict)
        event_id = d.pop("event_id")

        stream_id = d.pop("stream_id")

        type_ = d.pop("type")

        created_at = isoparse(d.pop("created_at"))

        actor = d.pop("actor", UNSET)

        message = d.pop("message", UNSET)

        _before = d.pop("before", UNSET)
        before: StreamEventBefore | Unset
        if isinstance(_before, Unset):
            before = UNSET
        else:
            before = StreamEventBefore.from_dict(_before)

        _after = d.pop("after", UNSET)
        after: StreamEventAfter | Unset
        if isinstance(_after, Unset):
            after = UNSET
        else:
            after = StreamEventAfter.from_dict(_after)

        stream_event = cls(
            event_id=event_id,
            stream_id=stream_id,
            type_=type_,
            created_at=created_at,
            actor=actor,
            message=message,
            before=before,
            after=after,
        )

        stream_event.additional_properties = d
        return stream_event

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
