from __future__ import annotations

from collections.abc import Mapping
from io import BytesIO
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from .. import types
from ..models.post_openapi_v1_uploads_body_unpack import PostOpenapiV1UploadsBodyUnpack
from ..types import UNSET, File, Unset

T = TypeVar("T", bound="PostOpenapiV1UploadsBody")


@_attrs_define
class PostOpenapiV1UploadsBody:
    """
    Attributes:
        file (File): File content (max 256 MB).
        unpack (PostOpenapiV1UploadsBodyUnpack | Unset): Unpack the uploaded zip into a directory URI.
    """

    file: File
    unpack: PostOpenapiV1UploadsBodyUnpack | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        file = self.file.to_tuple()

        unpack: str | Unset = UNSET
        if not isinstance(self.unpack, Unset):
            unpack = self.unpack.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "file": file,
            }
        )
        if unpack is not UNSET:
            field_dict["unpack"] = unpack

        return field_dict

    def to_multipart(self) -> types.RequestFiles:
        files: types.RequestFiles = []

        files.append(("file", self.file.to_tuple()))

        if not isinstance(self.unpack, Unset):
            files.append(
                ("unpack", (None, str(self.unpack.value).encode(), "text/plain"))
            )

        for prop_name, prop in self.additional_properties.items():
            files.append((prop_name, (None, str(prop).encode(), "text/plain")))

        return files

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        file = File(payload=BytesIO(d.pop("file")))

        _unpack = d.pop("unpack", UNSET)
        unpack: PostOpenapiV1UploadsBodyUnpack | Unset
        if isinstance(_unpack, Unset):
            unpack = UNSET
        else:
            unpack = PostOpenapiV1UploadsBodyUnpack(_unpack)

        post_openapi_v1_uploads_body = cls(
            file=file,
            unpack=unpack,
        )

        post_openapi_v1_uploads_body.additional_properties = d
        return post_openapi_v1_uploads_body

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
