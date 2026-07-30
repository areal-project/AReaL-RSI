from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.skill_source_kind import SkillSourceKind
from ..types import UNSET, Unset

T = TypeVar("T", bound="SkillSource")


@_attrs_define
class SkillSource:
    """
    Attributes:
        kind (SkillSourceKind | Unset):
        uri (str | Unset):
    """

    kind: SkillSourceKind | Unset = UNSET
    uri: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        kind: str | Unset = UNSET
        if not isinstance(self.kind, Unset):
            kind = self.kind.value

        uri = self.uri

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if kind is not UNSET:
            field_dict["kind"] = kind
        if uri is not UNSET:
            field_dict["uri"] = uri

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        _kind = d.pop("kind", UNSET)
        kind: SkillSourceKind | Unset
        if isinstance(_kind, Unset):
            kind = UNSET
        else:
            kind = SkillSourceKind(_kind)

        uri = d.pop("uri", UNSET)

        skill_source = cls(
            kind=kind,
            uri=uri,
        )

        skill_source.additional_properties = d
        return skill_source

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
