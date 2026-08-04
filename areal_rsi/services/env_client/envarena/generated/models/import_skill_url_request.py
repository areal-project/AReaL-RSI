from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.import_skill_url_request_metadata import ImportSkillURLRequestMetadata


T = TypeVar("T", bound="ImportSkillURLRequest")


@_attrs_define
class ImportSkillURLRequest:
    """
    Attributes:
        url (str):
        key (str | Unset):
        version (str | Unset):  Default: '1.0.0'.
        name (str | Unset):
        description (str | Unset):
        tags (list[str] | Unset):
        metadata (ImportSkillURLRequestMetadata | Unset):
        storage_ref (str | Unset): Registry material storage binding. `default` is the immutable OSS_* environment
            configuration.
        publish (bool | Unset):
    """

    url: str
    key: str | Unset = UNSET
    version: str | Unset = "1.0.0"
    name: str | Unset = UNSET
    description: str | Unset = UNSET
    tags: list[str] | Unset = UNSET
    metadata: ImportSkillURLRequestMetadata | Unset = UNSET
    storage_ref: str | Unset = UNSET
    publish: bool | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        url = self.url

        key = self.key

        version = self.version

        name = self.name

        description = self.description

        tags: list[str] | Unset = UNSET
        if not isinstance(self.tags, Unset):
            tags = self.tags

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

        storage_ref = self.storage_ref

        publish = self.publish

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "url": url,
            }
        )
        if key is not UNSET:
            field_dict["key"] = key
        if version is not UNSET:
            field_dict["version"] = version
        if name is not UNSET:
            field_dict["name"] = name
        if description is not UNSET:
            field_dict["description"] = description
        if tags is not UNSET:
            field_dict["tags"] = tags
        if metadata is not UNSET:
            field_dict["metadata"] = metadata
        if storage_ref is not UNSET:
            field_dict["storage_ref"] = storage_ref
        if publish is not UNSET:
            field_dict["publish"] = publish

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.import_skill_url_request_metadata import (
            ImportSkillURLRequestMetadata,
        )

        d = dict(src_dict)
        url = d.pop("url")

        key = d.pop("key", UNSET)

        version = d.pop("version", UNSET)

        name = d.pop("name", UNSET)

        description = d.pop("description", UNSET)

        tags = cast(list[str], d.pop("tags", UNSET))

        _metadata = d.pop("metadata", UNSET)
        metadata: ImportSkillURLRequestMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = ImportSkillURLRequestMetadata.from_dict(_metadata)

        storage_ref = d.pop("storage_ref", UNSET)

        publish = d.pop("publish", UNSET)

        import_skill_url_request = cls(
            url=url,
            key=key,
            version=version,
            name=name,
            description=description,
            tags=tags,
            metadata=metadata,
            storage_ref=storage_ref,
            publish=publish,
        )

        import_skill_url_request.additional_properties = d
        return import_skill_url_request

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
