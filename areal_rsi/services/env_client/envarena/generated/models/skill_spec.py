from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.object_ref import ObjectRef
    from ..models.skill_manifest import SkillManifest
    from ..models.skill_source import SkillSource
    from ..models.skill_spec_metadata import SkillSpecMetadata


T = TypeVar("T", bound="SkillSpec")


@_attrs_define
class SkillSpec:
    """
    Attributes:
        bundle (ObjectRef):
        manifest (SkillManifest):
        source (SkillSource | Unset):
        metadata (SkillSpecMetadata | Unset):
    """

    bundle: ObjectRef
    manifest: SkillManifest
    source: SkillSource | Unset = UNSET
    metadata: SkillSpecMetadata | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        bundle = self.bundle.to_dict()

        manifest = self.manifest.to_dict()

        source: dict[str, Any] | Unset = UNSET
        if not isinstance(self.source, Unset):
            source = self.source.to_dict()

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "bundle": bundle,
                "manifest": manifest,
            }
        )
        if source is not UNSET:
            field_dict["source"] = source
        if metadata is not UNSET:
            field_dict["metadata"] = metadata

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.object_ref import ObjectRef
        from ..models.skill_manifest import SkillManifest
        from ..models.skill_source import SkillSource
        from ..models.skill_spec_metadata import SkillSpecMetadata

        d = dict(src_dict)
        bundle = ObjectRef.from_dict(d.pop("bundle"))

        manifest = SkillManifest.from_dict(d.pop("manifest"))

        _source = d.pop("source", UNSET)
        source: SkillSource | Unset
        if isinstance(_source, Unset):
            source = UNSET
        else:
            source = SkillSource.from_dict(_source)

        _metadata = d.pop("metadata", UNSET)
        metadata: SkillSpecMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = SkillSpecMetadata.from_dict(_metadata)

        skill_spec = cls(
            bundle=bundle,
            manifest=manifest,
            source=source,
            metadata=metadata,
        )

        skill_spec.additional_properties = d
        return skill_spec

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
