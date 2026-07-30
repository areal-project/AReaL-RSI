from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.system_prompt_spec_kind import SystemPromptSpecKind
from ..types import UNSET, Unset

T = TypeVar("T", bound="SystemPromptSpec")


@_attrs_define
class SystemPromptSpec:
    """
    Attributes:
        kind (SystemPromptSpecKind | Unset):  Default: SystemPromptSpecKind.INLINE.
        content (str | Unset):
        uri (str | Unset):
        digest (str | Unset):
    """

    kind: SystemPromptSpecKind | Unset = SystemPromptSpecKind.INLINE
    content: str | Unset = UNSET
    uri: str | Unset = UNSET
    digest: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        kind: str | Unset = UNSET
        if not isinstance(self.kind, Unset):
            kind = self.kind.value

        content = self.content

        uri = self.uri

        digest = self.digest

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if kind is not UNSET:
            field_dict["kind"] = kind
        if content is not UNSET:
            field_dict["content"] = content
        if uri is not UNSET:
            field_dict["uri"] = uri
        if digest is not UNSET:
            field_dict["digest"] = digest

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        _kind = d.pop("kind", UNSET)
        kind: SystemPromptSpecKind | Unset
        if isinstance(_kind, Unset):
            kind = UNSET
        else:
            kind = SystemPromptSpecKind(_kind)

        content = d.pop("content", UNSET)

        uri = d.pop("uri", UNSET)

        digest = d.pop("digest", UNSET)

        system_prompt_spec = cls(
            kind=kind,
            content=content,
            uri=uri,
            digest=digest,
        )

        system_prompt_spec.additional_properties = d
        return system_prompt_spec

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
