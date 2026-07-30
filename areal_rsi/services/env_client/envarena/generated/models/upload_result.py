from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="UploadResult")


@_attrs_define
class UploadResult:
    """
    Attributes:
        uri (str): Prefix-relative URI of the staged object; directory URIs end with /. Example:
            uploads/260709/abc123/Target.md.
        name (str | Unset):
        size (int | Unset):
        sha256 (str | Unset):
        unpack (bool | Unset):
    """

    uri: str
    name: str | Unset = UNSET
    size: int | Unset = UNSET
    sha256: str | Unset = UNSET
    unpack: bool | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        uri = self.uri

        name = self.name

        size = self.size

        sha256 = self.sha256

        unpack = self.unpack

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "uri": uri,
            }
        )
        if name is not UNSET:
            field_dict["name"] = name
        if size is not UNSET:
            field_dict["size"] = size
        if sha256 is not UNSET:
            field_dict["sha256"] = sha256
        if unpack is not UNSET:
            field_dict["unpack"] = unpack

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        uri = d.pop("uri")

        name = d.pop("name", UNSET)

        size = d.pop("size", UNSET)

        sha256 = d.pop("sha256", UNSET)

        unpack = d.pop("unpack", UNSET)

        upload_result = cls(
            uri=uri,
            name=name,
            size=size,
            sha256=sha256,
            unpack=unpack,
        )

        upload_result.additional_properties = d
        return upload_result

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
