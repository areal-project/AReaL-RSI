from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.tool_version import ToolVersion


T = TypeVar("T", bound="GetOpenapiV1ToolsKeyVersionsResponse200")


@_attrs_define
class GetOpenapiV1ToolsKeyVersionsResponse200:
    """
    Attributes:
        items (list[ToolVersion] | Unset):
    """

    items: list[ToolVersion] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        items: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.items, Unset):
            items = []
            for items_item_data in self.items:
                items_item = items_item_data.to_dict()
                items.append(items_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if items is not UNSET:
            field_dict["items"] = items

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.tool_version import ToolVersion

        d = dict(src_dict)
        _items = d.pop("items", UNSET)
        items: list[ToolVersion] | Unset = UNSET
        if _items is not UNSET:
            items = []
            for items_item_data in _items:
                items_item = ToolVersion.from_dict(items_item_data)

                items.append(items_item)

        get_openapi_v1_tools_key_versions_response_200 = cls(
            items=items,
        )

        get_openapi_v1_tools_key_versions_response_200.additional_properties = d
        return get_openapi_v1_tools_key_versions_response_200

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
