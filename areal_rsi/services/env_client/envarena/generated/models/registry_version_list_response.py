from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.version_meta import VersionMeta


T = TypeVar("T", bound="RegistryVersionListResponse")


@_attrs_define
class RegistryVersionListResponse:
    """
    Attributes:
        total (int): Total matched entity count before pagination.
        offset (int): Applied page offset.
        limit (int): Applied page size. Defaults to 20.
        items (list[VersionMeta]): Version metadata only; fetch the existing key/version endpoint for the full spec.
    """

    total: int
    offset: int
    limit: int
    items: list[VersionMeta]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        total = self.total

        offset = self.offset

        limit = self.limit

        items = []
        for items_item_data in self.items:
            items_item = items_item_data.to_dict()
            items.append(items_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "total": total,
                "offset": offset,
                "limit": limit,
                "items": items,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.version_meta import VersionMeta

        d = dict(src_dict)
        total = d.pop("total")

        offset = d.pop("offset")

        limit = d.pop("limit")

        items = []
        _items = d.pop("items")
        for items_item_data in _items:
            items_item = VersionMeta.from_dict(items_item_data)

            items.append(items_item)

        registry_version_list_response = cls(
            total=total,
            offset=offset,
            limit=limit,
            items=items,
        )

        registry_version_list_response.additional_properties = d
        return registry_version_list_response

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
