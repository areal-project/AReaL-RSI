from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.runtime_image_kind import RuntimeImageKind
from ..types import UNSET, Unset

T = TypeVar("T", bound="RuntimeImage")


@_attrs_define
class RuntimeImage:
    """
    Attributes:
        kind (RuntimeImageKind):
        ref (str):  Example: registry.example/rewards/visual-reward:1.0.0.
        command (str | Unset): Optional container startup command. For container_image, Arena defaults to sleep infinity
            so the sandbox stays alive for exec. Example: sleep infinity.
    """

    kind: RuntimeImageKind
    ref: str
    command: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind.value

        ref = self.ref

        command = self.command

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "kind": kind,
                "ref": ref,
            }
        )
        if command is not UNSET:
            field_dict["command"] = command

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        kind = RuntimeImageKind(d.pop("kind"))

        ref = d.pop("ref")

        command = d.pop("command", UNSET)

        runtime_image = cls(
            kind=kind,
            ref=ref,
            command=command,
        )

        runtime_image.additional_properties = d
        return runtime_image

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
