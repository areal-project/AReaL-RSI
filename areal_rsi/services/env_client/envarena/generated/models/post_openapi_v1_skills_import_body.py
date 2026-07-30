from __future__ import annotations

from collections.abc import Mapping
from io import BytesIO
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from .. import types
from ..types import UNSET, File, Unset

T = TypeVar("T", bound="PostOpenapiV1SkillsImportBody")


@_attrs_define
class PostOpenapiV1SkillsImportBody:
    """
    Attributes:
        file (File):
        key (str | Unset):
        version (str | Unset):
        name (str | Unset):
        description (str | Unset):
        tags (str | Unset): Comma-separated tags.
        publish (bool | Unset):
    """

    file: File
    key: str | Unset = UNSET
    version: str | Unset = UNSET
    name: str | Unset = UNSET
    description: str | Unset = UNSET
    tags: str | Unset = UNSET
    publish: bool | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        file = self.file.to_tuple()

        key = self.key

        version = self.version

        name = self.name

        description = self.description

        tags = self.tags

        publish = self.publish

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "file": file,
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
        if publish is not UNSET:
            field_dict["publish"] = publish

        return field_dict

    def to_multipart(self) -> types.RequestFiles:
        files: types.RequestFiles = []

        files.append(("file", self.file.to_tuple()))

        if not isinstance(self.key, Unset):
            files.append(("key", (None, str(self.key).encode(), "text/plain")))

        if not isinstance(self.version, Unset):
            files.append(("version", (None, str(self.version).encode(), "text/plain")))

        if not isinstance(self.name, Unset):
            files.append(("name", (None, str(self.name).encode(), "text/plain")))

        if not isinstance(self.description, Unset):
            files.append(
                ("description", (None, str(self.description).encode(), "text/plain"))
            )

        if not isinstance(self.tags, Unset):
            files.append(("tags", (None, str(self.tags).encode(), "text/plain")))

        if not isinstance(self.publish, Unset):
            files.append(("publish", (None, str(self.publish).encode(), "text/plain")))

        for prop_name, prop in self.additional_properties.items():
            files.append((prop_name, (None, str(prop).encode(), "text/plain")))

        return files

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        file = File(payload=BytesIO(d.pop("file")))

        key = d.pop("key", UNSET)

        version = d.pop("version", UNSET)

        name = d.pop("name", UNSET)

        description = d.pop("description", UNSET)

        tags = d.pop("tags", UNSET)

        publish = d.pop("publish", UNSET)

        post_openapi_v1_skills_import_body = cls(
            file=file,
            key=key,
            version=version,
            name=name,
            description=description,
            tags=tags,
            publish=publish,
        )

        post_openapi_v1_skills_import_body.additional_properties = d
        return post_openapi_v1_skills_import_body

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
