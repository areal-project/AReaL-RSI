from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="UploadManifestFilesItem")


@_attrs_define
class UploadManifestFilesItem:
    """
    Attributes:
        path (str): Slash-separated path relative to the directory root; must not escape it.
        size (int): Exact byte size (max 256 MB per file, 1 GB total).
        md5 (str | Unset): Base64 16-byte MD5 digest. Required by /uploads/presign (folded into the signature).
        sha256 (str | Unset): Optional client-declared hex digest, echoed by /uploads/complete for kind=file.
    """

    path: str
    size: int
    md5: str | Unset = UNSET
    sha256: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        path = self.path

        size = self.size

        md5 = self.md5

        sha256 = self.sha256

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "path": path,
                "size": size,
            }
        )
        if md5 is not UNSET:
            field_dict["md5"] = md5
        if sha256 is not UNSET:
            field_dict["sha256"] = sha256

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        path = d.pop("path")

        size = d.pop("size")

        md5 = d.pop("md5", UNSET)

        sha256 = d.pop("sha256", UNSET)

        upload_manifest_files_item = cls(
            path=path,
            size=size,
            md5=md5,
            sha256=sha256,
        )

        upload_manifest_files_item.additional_properties = d
        return upload_manifest_files_item

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
