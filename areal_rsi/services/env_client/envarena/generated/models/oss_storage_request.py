from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="OSSStorageRequest")


@_attrs_define
class OSSStorageRequest:
    """
    Attributes:
        storage_id (str):
        name (str):
        bucket (str):
        endpoint (str):
        mount_id (str):
        presign_endpoint (str | Unset):
        prefix (str | Unset):
        access_key_id (str | Unset):
        access_key_secret (str | Unset):
    """

    storage_id: str
    name: str
    bucket: str
    endpoint: str
    mount_id: str
    presign_endpoint: str | Unset = UNSET
    prefix: str | Unset = UNSET
    access_key_id: str | Unset = UNSET
    access_key_secret: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        storage_id = self.storage_id

        name = self.name

        bucket = self.bucket

        endpoint = self.endpoint

        mount_id = self.mount_id

        presign_endpoint = self.presign_endpoint

        prefix = self.prefix

        access_key_id = self.access_key_id

        access_key_secret = self.access_key_secret

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "storage_id": storage_id,
                "name": name,
                "bucket": bucket,
                "endpoint": endpoint,
                "mount_id": mount_id,
            }
        )
        if presign_endpoint is not UNSET:
            field_dict["presign_endpoint"] = presign_endpoint
        if prefix is not UNSET:
            field_dict["prefix"] = prefix
        if access_key_id is not UNSET:
            field_dict["access_key_id"] = access_key_id
        if access_key_secret is not UNSET:
            field_dict["access_key_secret"] = access_key_secret

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        storage_id = d.pop("storage_id")

        name = d.pop("name")

        bucket = d.pop("bucket")

        endpoint = d.pop("endpoint")

        mount_id = d.pop("mount_id")

        presign_endpoint = d.pop("presign_endpoint", UNSET)

        prefix = d.pop("prefix", UNSET)

        access_key_id = d.pop("access_key_id", UNSET)

        access_key_secret = d.pop("access_key_secret", UNSET)

        oss_storage_request = cls(
            storage_id=storage_id,
            name=name,
            bucket=bucket,
            endpoint=endpoint,
            mount_id=mount_id,
            presign_endpoint=presign_endpoint,
            prefix=prefix,
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
        )

        oss_storage_request.additional_properties = d
        return oss_storage_request

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
