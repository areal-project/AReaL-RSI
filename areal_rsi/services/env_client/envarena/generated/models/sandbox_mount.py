from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="SandboxMount")


@_attrs_define
class SandboxMount:
    """
    Attributes:
        storage_ref (str): Effective EnvArena OSS storage reference selected by the PlayForge runtime profile.
        oss_path (str): Effective normalized OSS path sent to ARCA.
        mount_path (str): Effective absolute sandbox mount target.
        read_only (bool): True for system prompt, skills, and controlled input mounts; false only for the fixed
            `/output` release and `/envarena/out` telemetry mounts.
    """

    storage_ref: str
    oss_path: str
    mount_path: str
    read_only: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        storage_ref = self.storage_ref

        oss_path = self.oss_path

        mount_path = self.mount_path

        read_only = self.read_only

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "storage_ref": storage_ref,
                "oss_path": oss_path,
                "mount_path": mount_path,
                "read_only": read_only,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        storage_ref = d.pop("storage_ref")

        oss_path = d.pop("oss_path")

        mount_path = d.pop("mount_path")

        read_only = d.pop("read_only")

        sandbox_mount = cls(
            storage_ref=storage_ref,
            oss_path=oss_path,
            mount_path=mount_path,
            read_only=read_only,
        )

        sandbox_mount.additional_properties = d
        return sandbox_mount

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
