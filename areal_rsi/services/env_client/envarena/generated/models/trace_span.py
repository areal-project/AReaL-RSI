from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.trace_span_attributes import TraceSpanAttributes
    from ..models.trace_span_events_item import TraceSpanEventsItem


T = TypeVar("T", bound="TraceSpan")


@_attrs_define
class TraceSpan:
    """
    Attributes:
        span_id (str):
        name (str):
        trace_id (str | Unset):
        parent_span_id (str | Unset):
        kind (str | Unset):
        service_name (str | Unset):
        scope_name (str | Unset):
        start_unix_nano (int | Unset):
        end_unix_nano (int | Unset):
        duration_ms (float | Unset):
        status_code (str | Unset):
        status_message (str | Unset):
        attributes (TraceSpanAttributes | Unset):
        events (list[TraceSpanEventsItem] | Unset):
    """

    span_id: str
    name: str
    trace_id: str | Unset = UNSET
    parent_span_id: str | Unset = UNSET
    kind: str | Unset = UNSET
    service_name: str | Unset = UNSET
    scope_name: str | Unset = UNSET
    start_unix_nano: int | Unset = UNSET
    end_unix_nano: int | Unset = UNSET
    duration_ms: float | Unset = UNSET
    status_code: str | Unset = UNSET
    status_message: str | Unset = UNSET
    attributes: TraceSpanAttributes | Unset = UNSET
    events: list[TraceSpanEventsItem] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        span_id = self.span_id

        name = self.name

        trace_id = self.trace_id

        parent_span_id = self.parent_span_id

        kind = self.kind

        service_name = self.service_name

        scope_name = self.scope_name

        start_unix_nano = self.start_unix_nano

        end_unix_nano = self.end_unix_nano

        duration_ms = self.duration_ms

        status_code = self.status_code

        status_message = self.status_message

        attributes: dict[str, Any] | Unset = UNSET
        if not isinstance(self.attributes, Unset):
            attributes = self.attributes.to_dict()

        events: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.events, Unset):
            events = []
            for events_item_data in self.events:
                events_item = events_item_data.to_dict()
                events.append(events_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "span_id": span_id,
                "name": name,
            }
        )
        if trace_id is not UNSET:
            field_dict["trace_id"] = trace_id
        if parent_span_id is not UNSET:
            field_dict["parent_span_id"] = parent_span_id
        if kind is not UNSET:
            field_dict["kind"] = kind
        if service_name is not UNSET:
            field_dict["service_name"] = service_name
        if scope_name is not UNSET:
            field_dict["scope_name"] = scope_name
        if start_unix_nano is not UNSET:
            field_dict["start_unix_nano"] = start_unix_nano
        if end_unix_nano is not UNSET:
            field_dict["end_unix_nano"] = end_unix_nano
        if duration_ms is not UNSET:
            field_dict["duration_ms"] = duration_ms
        if status_code is not UNSET:
            field_dict["status_code"] = status_code
        if status_message is not UNSET:
            field_dict["status_message"] = status_message
        if attributes is not UNSET:
            field_dict["attributes"] = attributes
        if events is not UNSET:
            field_dict["events"] = events

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.trace_span_attributes import TraceSpanAttributes
        from ..models.trace_span_events_item import TraceSpanEventsItem

        d = dict(src_dict)
        span_id = d.pop("span_id")

        name = d.pop("name")

        trace_id = d.pop("trace_id", UNSET)

        parent_span_id = d.pop("parent_span_id", UNSET)

        kind = d.pop("kind", UNSET)

        service_name = d.pop("service_name", UNSET)

        scope_name = d.pop("scope_name", UNSET)

        start_unix_nano = d.pop("start_unix_nano", UNSET)

        end_unix_nano = d.pop("end_unix_nano", UNSET)

        duration_ms = d.pop("duration_ms", UNSET)

        status_code = d.pop("status_code", UNSET)

        status_message = d.pop("status_message", UNSET)

        _attributes = d.pop("attributes", UNSET)
        attributes: TraceSpanAttributes | Unset
        if isinstance(_attributes, Unset):
            attributes = UNSET
        else:
            attributes = TraceSpanAttributes.from_dict(_attributes)

        _events = d.pop("events", UNSET)
        events: list[TraceSpanEventsItem] | Unset = UNSET
        if _events is not UNSET:
            events = []
            for events_item_data in _events:
                events_item = TraceSpanEventsItem.from_dict(events_item_data)

                events.append(events_item)

        trace_span = cls(
            span_id=span_id,
            name=name,
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            kind=kind,
            service_name=service_name,
            scope_name=scope_name,
            start_unix_nano=start_unix_nano,
            end_unix_nano=end_unix_nano,
            duration_ms=duration_ms,
            status_code=status_code,
            status_message=status_message,
            attributes=attributes,
            events=events,
        )

        trace_span.additional_properties = d
        return trace_span

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
