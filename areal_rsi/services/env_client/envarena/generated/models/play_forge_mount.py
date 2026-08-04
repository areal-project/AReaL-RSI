from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="PlayForgeMount")


@_attrs_define
class PlayForgeMount:
    """
    Attributes:
        oss_path (str): PlayForge OSS path inside ARENA_PLAYFORGE_OSS_PREFIX for a read-only input mount.
        mount_path (str): Absolute sandbox path; the mount is always read-only.
    """

    oss_path: str
    mount_path: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        oss_path = self.oss_path

        mount_path = self.mount_path

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "oss_path": oss_path,
                "mount_path": mount_path,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        oss_path = d.pop("oss_path")

        mount_path = d.pop("mount_path")

        play_forge_mount = cls(
            oss_path=oss_path,
            mount_path=mount_path,
        )

        play_forge_mount.additional_properties = d
        return play_forge_mount

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
