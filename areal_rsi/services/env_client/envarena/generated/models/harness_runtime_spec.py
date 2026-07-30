from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.harness_runtime_spec_type import HarnessRuntimeSpecType
from ..types import UNSET, Unset

T = TypeVar("T", bound="HarnessRuntimeSpec")


@_attrs_define
class HarnessRuntimeSpec:
    """
    Attributes:
        type_ (HarnessRuntimeSpecType): Agent runtime family. codex is schema-reserved for a future runner.
        uri (str): OSS URI of the runtime executable, for example a specific claude-code binary version.
        digest (str | Unset):
    """

    type_: HarnessRuntimeSpecType
    uri: str
    digest: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        type_ = self.type_.value

        uri = self.uri

        digest = self.digest

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "type": type_,
                "uri": uri,
            }
        )
        if digest is not UNSET:
            field_dict["digest"] = digest

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        type_ = HarnessRuntimeSpecType(d.pop("type"))

        uri = d.pop("uri")

        digest = d.pop("digest", UNSET)

        harness_runtime_spec = cls(
            type_=type_,
            uri=uri,
            digest=digest,
        )

        harness_runtime_spec.additional_properties = d
        return harness_runtime_spec

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
