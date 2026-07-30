from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ObjectRef")


@_attrs_define
class ObjectRef:
    """
    Attributes:
        uri (str):  Example: oss://bucket/env/feiji/1.0.0/bundle/.
        digest (str | Unset):
        media_type (str | Unset):
    """

    uri: str
    digest: str | Unset = UNSET
    media_type: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        uri = self.uri

        digest = self.digest

        media_type = self.media_type

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "uri": uri,
            }
        )
        if digest is not UNSET:
            field_dict["digest"] = digest
        if media_type is not UNSET:
            field_dict["media_type"] = media_type

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        uri = d.pop("uri")

        digest = d.pop("digest", UNSET)

        media_type = d.pop("media_type", UNSET)

        object_ref = cls(
            uri=uri,
            digest=digest,
            media_type=media_type,
        )

        object_ref.additional_properties = d
        return object_ref

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
