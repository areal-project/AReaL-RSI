from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.tool_manifest_kind import ToolManifestKind
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.tool_manifest_metadata import ToolManifestMetadata


T = TypeVar("T", bound="ToolManifest")


@_attrs_define
class ToolManifest:
    """
    Attributes:
        name (str):
        description (str):
        kind (ToolManifestKind | Unset):
        metadata (ToolManifestMetadata | Unset):
    """

    name: str
    description: str
    kind: ToolManifestKind | Unset = UNSET
    metadata: ToolManifestMetadata | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        description = self.description

        kind: str | Unset = UNSET
        if not isinstance(self.kind, Unset):
            kind = self.kind.value

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
        if kind is not UNSET:
            field_dict["kind"] = kind
        if metadata is not UNSET:
            field_dict["metadata"] = metadata

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.tool_manifest_metadata import ToolManifestMetadata

        d = dict(src_dict)
        name = d.pop("name")

        description = d.pop("description")

        _kind = d.pop("kind", UNSET)
        kind: ToolManifestKind | Unset
        if isinstance(_kind, Unset):
            kind = UNSET
        else:
            kind = ToolManifestKind(_kind)

        _metadata = d.pop("metadata", UNSET)
        metadata: ToolManifestMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = ToolManifestMetadata.from_dict(_metadata)

        tool_manifest = cls(
            name=name,
            description=description,
            kind=kind,
            metadata=metadata,
        )

        tool_manifest.additional_properties = d
        return tool_manifest

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
