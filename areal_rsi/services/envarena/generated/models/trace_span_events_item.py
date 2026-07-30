from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.trace_span_events_item_attributes import TraceSpanEventsItemAttributes


T = TypeVar("T", bound="TraceSpanEventsItem")


@_attrs_define
class TraceSpanEventsItem:
    """
    Attributes:
        name (str | Unset):
        time_unix_nano (int | Unset):
        attributes (TraceSpanEventsItemAttributes | Unset):
    """

    name: str | Unset = UNSET
    time_unix_nano: int | Unset = UNSET
    attributes: TraceSpanEventsItemAttributes | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        time_unix_nano = self.time_unix_nano

        attributes: dict[str, Any] | Unset = UNSET
        if not isinstance(self.attributes, Unset):
            attributes = self.attributes.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if name is not UNSET:
            field_dict["name"] = name
        if time_unix_nano is not UNSET:
            field_dict["time_unix_nano"] = time_unix_nano
        if attributes is not UNSET:
            field_dict["attributes"] = attributes

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.trace_span_events_item_attributes import (
            TraceSpanEventsItemAttributes,
        )

        d = dict(src_dict)
        name = d.pop("name", UNSET)

        time_unix_nano = d.pop("time_unix_nano", UNSET)

        _attributes = d.pop("attributes", UNSET)
        attributes: TraceSpanEventsItemAttributes | Unset
        if isinstance(_attributes, Unset):
            attributes = UNSET
        else:
            attributes = TraceSpanEventsItemAttributes.from_dict(_attributes)

        trace_span_events_item = cls(
            name=name,
            time_unix_nano=time_unix_nano,
            attributes=attributes,
        )

        trace_span_events_item.additional_properties = d
        return trace_span_events_item

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
