from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.stream import Stream
    from ..models.stream_stats import StreamStats


T = TypeVar("T", bound="GetOpenapiV1StreamsStreamIdResponse200")


@_attrs_define
class GetOpenapiV1StreamsStreamIdResponse200:
    """
    Attributes:
        stream (Stream):
        stats (StreamStats | Unset):
    """

    stream: Stream
    stats: StreamStats | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        stream = self.stream.to_dict()

        stats: dict[str, Any] | Unset = UNSET
        if not isinstance(self.stats, Unset):
            stats = self.stats.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "stream": stream,
            }
        )
        if stats is not UNSET:
            field_dict["stats"] = stats

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.stream import Stream
        from ..models.stream_stats import StreamStats

        d = dict(src_dict)
        stream = Stream.from_dict(d.pop("stream"))

        _stats = d.pop("stats", UNSET)
        stats: StreamStats | Unset
        if isinstance(_stats, Unset):
            stats = UNSET
        else:
            stats = StreamStats.from_dict(_stats)

        get_openapi_v1_streams_stream_id_response_200 = cls(
            stream=stream,
            stats=stats,
        )

        get_openapi_v1_streams_stream_id_response_200.additional_properties = d
        return get_openapi_v1_streams_stream_id_response_200

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
