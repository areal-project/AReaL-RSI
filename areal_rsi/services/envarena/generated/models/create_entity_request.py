from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.create_entity_request_metadata import CreateEntityRequestMetadata


T = TypeVar("T", bound="CreateEntityRequest")


@_attrs_define
class CreateEntityRequest:
    """
    Attributes:
        key (str):
        name (str):
        description (str | Unset):
        tags (list[str] | Unset):
        metadata (CreateEntityRequestMetadata | Unset):
    """

    key: str
    name: str
    description: str | Unset = UNSET
    tags: list[str] | Unset = UNSET
    metadata: CreateEntityRequestMetadata | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        key = self.key

        name = self.name

        description = self.description

        tags: list[str] | Unset = UNSET
        if not isinstance(self.tags, Unset):
            tags = self.tags

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "key": key,
                "name": name,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description
        if tags is not UNSET:
            field_dict["tags"] = tags
        if metadata is not UNSET:
            field_dict["metadata"] = metadata

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.create_entity_request_metadata import CreateEntityRequestMetadata

        d = dict(src_dict)
        key = d.pop("key")

        name = d.pop("name")

        description = d.pop("description", UNSET)

        tags = cast(list[str], d.pop("tags", UNSET))

        _metadata = d.pop("metadata", UNSET)
        metadata: CreateEntityRequestMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = CreateEntityRequestMetadata.from_dict(_metadata)

        create_entity_request = cls(
            key=key,
            name=name,
            description=description,
            tags=tags,
            metadata=metadata,
        )

        create_entity_request.additional_properties = d
        return create_entity_request

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
