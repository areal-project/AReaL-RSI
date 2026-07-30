from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.upload_presign_result_files_item_headers import (
        UploadPresignResultFilesItemHeaders,
    )


T = TypeVar("T", bound="UploadPresignResultFilesItem")


@_attrs_define
class UploadPresignResultFilesItem:
    """
    Attributes:
        path (str | Unset):
        key (str | Unset):
        url (str | Unset): Presigned PUT URL; upload bytes with exactly the accompanying headers.
        headers (UploadPresignResultFilesItemHeaders | Unset):
    """

    path: str | Unset = UNSET
    key: str | Unset = UNSET
    url: str | Unset = UNSET
    headers: UploadPresignResultFilesItemHeaders | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        path = self.path

        key = self.key

        url = self.url

        headers: dict[str, Any] | Unset = UNSET
        if not isinstance(self.headers, Unset):
            headers = self.headers.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if path is not UNSET:
            field_dict["path"] = path
        if key is not UNSET:
            field_dict["key"] = key
        if url is not UNSET:
            field_dict["url"] = url
        if headers is not UNSET:
            field_dict["headers"] = headers

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.upload_presign_result_files_item_headers import (
            UploadPresignResultFilesItemHeaders,
        )

        d = dict(src_dict)
        path = d.pop("path", UNSET)

        key = d.pop("key", UNSET)

        url = d.pop("url", UNSET)

        _headers = d.pop("headers", UNSET)
        headers: UploadPresignResultFilesItemHeaders | Unset
        if isinstance(_headers, Unset):
            headers = UNSET
        else:
            headers = UploadPresignResultFilesItemHeaders.from_dict(_headers)

        upload_presign_result_files_item = cls(
            path=path,
            key=key,
            url=url,
            headers=headers,
        )

        upload_presign_result_files_item.additional_properties = d
        return upload_presign_result_files_item

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
