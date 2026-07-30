from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.object_ref import ObjectRef
    from ..models.skill_source import SkillSource
    from ..models.tool_manifest import ToolManifest
    from ..models.tool_spec_metadata import ToolSpecMetadata


T = TypeVar("T", bound="ToolSpec")


@_attrs_define
class ToolSpec:
    """
    Attributes:
        manifest (ToolManifest):
        source (SkillSource | Unset):
        bundle (ObjectRef | Unset):
        mcp_config (ObjectRef | Unset):
        settings (ObjectRef | Unset):
        metadata (ToolSpecMetadata | Unset):
    """

    manifest: ToolManifest
    source: SkillSource | Unset = UNSET
    bundle: ObjectRef | Unset = UNSET
    mcp_config: ObjectRef | Unset = UNSET
    settings: ObjectRef | Unset = UNSET
    metadata: ToolSpecMetadata | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        manifest = self.manifest.to_dict()

        source: dict[str, Any] | Unset = UNSET
        if not isinstance(self.source, Unset):
            source = self.source.to_dict()

        bundle: dict[str, Any] | Unset = UNSET
        if not isinstance(self.bundle, Unset):
            bundle = self.bundle.to_dict()

        mcp_config: dict[str, Any] | Unset = UNSET
        if not isinstance(self.mcp_config, Unset):
            mcp_config = self.mcp_config.to_dict()

        settings: dict[str, Any] | Unset = UNSET
        if not isinstance(self.settings, Unset):
            settings = self.settings.to_dict()

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "manifest": manifest,
            }
        )
        if source is not UNSET:
            field_dict["source"] = source
        if bundle is not UNSET:
            field_dict["bundle"] = bundle
        if mcp_config is not UNSET:
            field_dict["mcp_config"] = mcp_config
        if settings is not UNSET:
            field_dict["settings"] = settings
        if metadata is not UNSET:
            field_dict["metadata"] = metadata

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.object_ref import ObjectRef
        from ..models.skill_source import SkillSource
        from ..models.tool_manifest import ToolManifest
        from ..models.tool_spec_metadata import ToolSpecMetadata

        d = dict(src_dict)
        manifest = ToolManifest.from_dict(d.pop("manifest"))

        _source = d.pop("source", UNSET)
        source: SkillSource | Unset
        if isinstance(_source, Unset):
            source = UNSET
        else:
            source = SkillSource.from_dict(_source)

        _bundle = d.pop("bundle", UNSET)
        bundle: ObjectRef | Unset
        if isinstance(_bundle, Unset):
            bundle = UNSET
        else:
            bundle = ObjectRef.from_dict(_bundle)

        _mcp_config = d.pop("mcp_config", UNSET)
        mcp_config: ObjectRef | Unset
        if isinstance(_mcp_config, Unset):
            mcp_config = UNSET
        else:
            mcp_config = ObjectRef.from_dict(_mcp_config)

        _settings = d.pop("settings", UNSET)
        settings: ObjectRef | Unset
        if isinstance(_settings, Unset):
            settings = UNSET
        else:
            settings = ObjectRef.from_dict(_settings)

        _metadata = d.pop("metadata", UNSET)
        metadata: ToolSpecMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = ToolSpecMetadata.from_dict(_metadata)

        tool_spec = cls(
            manifest=manifest,
            source=source,
            bundle=bundle,
            mcp_config=mcp_config,
            settings=settings,
            metadata=metadata,
        )

        tool_spec.additional_properties = d
        return tool_spec

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
