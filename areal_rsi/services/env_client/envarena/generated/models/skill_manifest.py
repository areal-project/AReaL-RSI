from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.skill_manifest_metadata import SkillManifestMetadata


T = TypeVar("T", bound="SkillManifest")


@_attrs_define
class SkillManifest:
    """
    Attributes:
        name (str):
        description (str):
        license_ (str | Unset):
        metadata (SkillManifestMetadata | Unset):
    """

    name: str
    description: str
    license_: str | Unset = UNSET
    metadata: SkillManifestMetadata | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        description = self.description

        license_ = self.license_

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
                "description": description,
            }
        )
        if license_ is not UNSET:
            field_dict["license"] = license_
        if metadata is not UNSET:
            field_dict["metadata"] = metadata

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.skill_manifest_metadata import SkillManifestMetadata

        d = dict(src_dict)
        name = d.pop("name")

        description = d.pop("description")

        license_ = d.pop("license", UNSET)

        _metadata = d.pop("metadata", UNSET)
        metadata: SkillManifestMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = SkillManifestMetadata.from_dict(_metadata)

        skill_manifest = cls(
            name=name,
            description=description,
            license_=license_,
            metadata=metadata,
        )

        skill_manifest.additional_properties = d
        return skill_manifest

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
