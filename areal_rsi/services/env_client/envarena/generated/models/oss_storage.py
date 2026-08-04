from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

T = TypeVar("T", bound="OSSStorage")


@_attrs_define
class OSSStorage:
    """
    Attributes:
        storage_id (str):
        name (str):
        bucket (str):
        endpoint (str):
        mount_id (str):
        secret_set (bool):
        default (bool):
        immutable (bool):
        reference_count (int):
        presign_endpoint (str | Unset):
        prefix (str | Unset):
        access_key_id_hint (str | Unset):
        created_at (datetime.datetime | Unset):
        updated_at (datetime.datetime | Unset):
    """

    storage_id: str
    name: str
    bucket: str
    endpoint: str
    mount_id: str
    secret_set: bool
    default: bool
    immutable: bool
    reference_count: int
    presign_endpoint: str | Unset = UNSET
    prefix: str | Unset = UNSET
    access_key_id_hint: str | Unset = UNSET
    created_at: datetime.datetime | Unset = UNSET
    updated_at: datetime.datetime | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        storage_id = self.storage_id

        name = self.name

        bucket = self.bucket

        endpoint = self.endpoint

        mount_id = self.mount_id

        secret_set = self.secret_set

        default = self.default

        immutable = self.immutable

        reference_count = self.reference_count

        presign_endpoint = self.presign_endpoint

        prefix = self.prefix

        access_key_id_hint = self.access_key_id_hint

        created_at: str | Unset = UNSET
        if not isinstance(self.created_at, Unset):
            created_at = self.created_at.isoformat()

        updated_at: str | Unset = UNSET
        if not isinstance(self.updated_at, Unset):
            updated_at = self.updated_at.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "storage_id": storage_id,
                "name": name,
                "bucket": bucket,
                "endpoint": endpoint,
                "mount_id": mount_id,
                "secret_set": secret_set,
                "default": default,
                "immutable": immutable,
                "reference_count": reference_count,
            }
        )
        if presign_endpoint is not UNSET:
            field_dict["presign_endpoint"] = presign_endpoint
        if prefix is not UNSET:
            field_dict["prefix"] = prefix
        if access_key_id_hint is not UNSET:
            field_dict["access_key_id_hint"] = access_key_id_hint
        if created_at is not UNSET:
            field_dict["created_at"] = created_at
        if updated_at is not UNSET:
            field_dict["updated_at"] = updated_at

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        storage_id = d.pop("storage_id")

        name = d.pop("name")

        bucket = d.pop("bucket")

        endpoint = d.pop("endpoint")

        mount_id = d.pop("mount_id")

        secret_set = d.pop("secret_set")

        default = d.pop("default")

        immutable = d.pop("immutable")

        reference_count = d.pop("reference_count")

        presign_endpoint = d.pop("presign_endpoint", UNSET)

        prefix = d.pop("prefix", UNSET)

        access_key_id_hint = d.pop("access_key_id_hint", UNSET)

        _created_at = d.pop("created_at", UNSET)
        created_at: datetime.datetime | Unset
        if isinstance(_created_at, Unset):
            created_at = UNSET
        else:
            created_at = isoparse(_created_at)

        _updated_at = d.pop("updated_at", UNSET)
        updated_at: datetime.datetime | Unset
        if isinstance(_updated_at, Unset):
            updated_at = UNSET
        else:
            updated_at = isoparse(_updated_at)

        oss_storage = cls(
            storage_id=storage_id,
            name=name,
            bucket=bucket,
            endpoint=endpoint,
            mount_id=mount_id,
            secret_set=secret_set,
            default=default,
            immutable=immutable,
            reference_count=reference_count,
            presign_endpoint=presign_endpoint,
            prefix=prefix,
            access_key_id_hint=access_key_id_hint,
            created_at=created_at,
            updated_at=updated_at,
        )

        oss_storage.additional_properties = d
        return oss_storage

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
