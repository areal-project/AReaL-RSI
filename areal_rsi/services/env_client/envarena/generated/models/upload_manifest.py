from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.upload_manifest_kind import UploadManifestKind
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.upload_manifest_files_item import UploadManifestFilesItem


T = TypeVar("T", bound="UploadManifest")


@_attrs_define
class UploadManifest:
    """
    Attributes:
        kind (UploadManifestKind):
        name (str): File name (kind=file) or directory name (kind=dir) under the scratch base.
        files (list[UploadManifestFilesItem]):
        base (str | Unset): Scratch base from the presign response. Required for /uploads/complete, ignored by
            /uploads/presign. Example: uploads/260716/0123456789abcdef.
    """

    kind: UploadManifestKind
    name: str
    files: list[UploadManifestFilesItem]
    base: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind.value

        name = self.name

        files = []
        for files_item_data in self.files:
            files_item = files_item_data.to_dict()
            files.append(files_item)

        base = self.base

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "kind": kind,
                "name": name,
                "files": files,
            }
        )
        if base is not UNSET:
            field_dict["base"] = base

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.upload_manifest_files_item import UploadManifestFilesItem

        d = dict(src_dict)
        kind = UploadManifestKind(d.pop("kind"))

        name = d.pop("name")

        files = []
        _files = d.pop("files")
        for files_item_data in _files:
            files_item = UploadManifestFilesItem.from_dict(files_item_data)

            files.append(files_item)

        base = d.pop("base", UNSET)

        upload_manifest = cls(
            kind=kind,
            name=name,
            files=files,
            base=base,
        )

        upload_manifest.additional_properties = d
        return upload_manifest

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
