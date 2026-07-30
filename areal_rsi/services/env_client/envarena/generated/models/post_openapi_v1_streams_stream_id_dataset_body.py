from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.stream_selector import StreamSelector


T = TypeVar("T", bound="PostOpenapiV1StreamsStreamIdDatasetBody")


@_attrs_define
class PostOpenapiV1StreamsStreamIdDatasetBody:
    """
    Attributes:
        selector (StreamSelector | Unset):
    """

    selector: StreamSelector | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        selector: dict[str, Any] | Unset = UNSET
        if not isinstance(self.selector, Unset):
            selector = self.selector.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if selector is not UNSET:
            field_dict["selector"] = selector

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.stream_selector import StreamSelector

        d = dict(src_dict)
        _selector = d.pop("selector", UNSET)
        selector: StreamSelector | Unset
        if isinstance(_selector, Unset):
            selector = UNSET
        else:
            selector = StreamSelector.from_dict(_selector)

        post_openapi_v1_streams_stream_id_dataset_body = cls(
            selector=selector,
        )

        post_openapi_v1_streams_stream_id_dataset_body.additional_properties = d
        return post_openapi_v1_streams_stream_id_dataset_body

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
