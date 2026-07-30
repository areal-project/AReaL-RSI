from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="DataSource")


@_attrs_define
class DataSource:
    """Object-store materialization override for Env bundles with non-standard layout.

    Attributes:
        kind (str | Unset):  Example: oss.
        bucket (str | Unset):
        env_prefix (str | Unset):
        oss_mount_id (str | Unset):
    """

    kind: str | Unset = UNSET
    bucket: str | Unset = UNSET
    env_prefix: str | Unset = UNSET
    oss_mount_id: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind

        bucket = self.bucket

        env_prefix = self.env_prefix

        oss_mount_id = self.oss_mount_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if kind is not UNSET:
            field_dict["kind"] = kind
        if bucket is not UNSET:
            field_dict["bucket"] = bucket
        if env_prefix is not UNSET:
            field_dict["env_prefix"] = env_prefix
        if oss_mount_id is not UNSET:
            field_dict["oss_mount_id"] = oss_mount_id

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        kind = d.pop("kind", UNSET)

        bucket = d.pop("bucket", UNSET)

        env_prefix = d.pop("env_prefix", UNSET)

        oss_mount_id = d.pop("oss_mount_id", UNSET)

        data_source = cls(
            kind=kind,
            bucket=bucket,
            env_prefix=env_prefix,
            oss_mount_id=oss_mount_id,
        )

        data_source.additional_properties = d
        return data_source

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
