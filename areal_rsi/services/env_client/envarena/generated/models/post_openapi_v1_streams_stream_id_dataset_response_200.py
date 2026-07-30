from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.stream_data_ref import StreamDataRef


T = TypeVar("T", bound="PostOpenapiV1StreamsStreamIdDatasetResponse200")


@_attrs_define
class PostOpenapiV1StreamsStreamIdDatasetResponse200:
    """
    Attributes:
        data_ids (list[str]):
        count (int): Number of items in this page (== len(data_ids)).
        total (int): Total matched data refs across all pages.
        offset (int):
        limit (int):
        items (list[StreamDataRef] | Unset):
    """

    data_ids: list[str]
    count: int
    total: int
    offset: int
    limit: int
    items: list[StreamDataRef] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data_ids = self.data_ids

        count = self.count

        total = self.total

        offset = self.offset

        limit = self.limit

        items: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.items, Unset):
            items = []
            for items_item_data in self.items:
                items_item = items_item_data.to_dict()
                items.append(items_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "data_ids": data_ids,
                "count": count,
                "total": total,
                "offset": offset,
                "limit": limit,
            }
        )
        if items is not UNSET:
            field_dict["items"] = items

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.stream_data_ref import StreamDataRef

        d = dict(src_dict)
        data_ids = cast(list[str], d.pop("data_ids"))

        count = d.pop("count")

        total = d.pop("total")

        offset = d.pop("offset")

        limit = d.pop("limit")

        _items = d.pop("items", UNSET)
        items: list[StreamDataRef] | Unset = UNSET
        if _items is not UNSET:
            items = []
            for items_item_data in _items:
                items_item = StreamDataRef.from_dict(items_item_data)

                items.append(items_item)

        post_openapi_v1_streams_stream_id_dataset_response_200 = cls(
            data_ids=data_ids,
            count=count,
            total=total,
            offset=offset,
            limit=limit,
            items=items,
        )

        post_openapi_v1_streams_stream_id_dataset_response_200.additional_properties = d
        return post_openapi_v1_streams_stream_id_dataset_response_200

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
