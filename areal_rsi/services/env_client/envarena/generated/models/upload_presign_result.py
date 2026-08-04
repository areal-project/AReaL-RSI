from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.upload_presign_result_kind import UploadPresignResultKind
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.upload_presign_result_files_item import UploadPresignResultFilesItem


T = TypeVar("T", bound="UploadPresignResult")


@_attrs_define
class UploadPresignResult:
    """
    Attributes:
        base (str):  Example: uploads/260716/0123456789abcdef.
        uri (str): Final URI to reference after completion; directory URIs end with /.
        files (list[UploadPresignResultFilesItem]):
        name (str | Unset):
        kind (UploadPresignResultKind | Unset):
        storage_ref (str | Unset): Registry material storage binding. `default` is the immutable OSS_* environment
            configuration.
        expires_at (datetime.datetime | Unset):
    """

    base: str
    uri: str
    files: list[UploadPresignResultFilesItem]
    name: str | Unset = UNSET
    kind: UploadPresignResultKind | Unset = UNSET
    storage_ref: str | Unset = UNSET
    expires_at: datetime.datetime | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        base = self.base

        uri = self.uri

        files = []
        for files_item_data in self.files:
            files_item = files_item_data.to_dict()
            files.append(files_item)

        name = self.name

        kind: str | Unset = UNSET
        if not isinstance(self.kind, Unset):
            kind = self.kind.value

        storage_ref = self.storage_ref

        expires_at: str | Unset = UNSET
        if not isinstance(self.expires_at, Unset):
            expires_at = self.expires_at.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "base": base,
                "uri": uri,
                "files": files,
            }
        )
        if name is not UNSET:
            field_dict["name"] = name
        if kind is not UNSET:
            field_dict["kind"] = kind
        if storage_ref is not UNSET:
            field_dict["storage_ref"] = storage_ref
        if expires_at is not UNSET:
            field_dict["expires_at"] = expires_at

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.upload_presign_result_files_item import (
            UploadPresignResultFilesItem,
        )

        d = dict(src_dict)
        base = d.pop("base")

        uri = d.pop("uri")

        files = []
        _files = d.pop("files")
        for files_item_data in _files:
            files_item = UploadPresignResultFilesItem.from_dict(files_item_data)

            files.append(files_item)

        name = d.pop("name", UNSET)

        _kind = d.pop("kind", UNSET)
        kind: UploadPresignResultKind | Unset
        if isinstance(_kind, Unset):
            kind = UNSET
        else:
            kind = UploadPresignResultKind(_kind)

        storage_ref = d.pop("storage_ref", UNSET)

        _expires_at = d.pop("expires_at", UNSET)
        expires_at: datetime.datetime | Unset
        if isinstance(_expires_at, Unset):
            expires_at = UNSET
        else:
            expires_at = isoparse(_expires_at)

        upload_presign_result = cls(
            base=base,
            uri=uri,
            files=files,
            name=name,
            kind=kind,
            storage_ref=storage_ref,
            expires_at=expires_at,
        )

        upload_presign_result.additional_properties = d
        return upload_presign_result

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
