from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.env_runtime_spec_kind import EnvRuntimeSpecKind
from ..types import UNSET, Unset

T = TypeVar("T", bound="EnvRuntimeSpec")


@_attrs_define
class EnvRuntimeSpec:
    """
    Attributes:
        kind (EnvRuntimeSpecKind):
        image (str | Unset): Required when kind=image. This is the user-provided Env image. Example:
            registry.example/envs/programbench-env:1.0.0.
        profile (str | Unset): Required when kind=image. Public runtime option key such as image_networked or
            image_offline. Example: image_networked.
        command (str | Unset): Optional keepalive command for image runtimes. Defaults to sleep infinity.
    """

    kind: EnvRuntimeSpecKind
    image: str | Unset = UNSET
    profile: str | Unset = UNSET
    command: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind.value

        image = self.image

        profile = self.profile

        command = self.command

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "kind": kind,
            }
        )
        if image is not UNSET:
            field_dict["image"] = image
        if profile is not UNSET:
            field_dict["profile"] = profile
        if command is not UNSET:
            field_dict["command"] = command

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        kind = EnvRuntimeSpecKind(d.pop("kind"))

        image = d.pop("image", UNSET)

        profile = d.pop("profile", UNSET)

        command = d.pop("command", UNSET)

        env_runtime_spec = cls(
            kind=kind,
            image=image,
            profile=profile,
            command=command,
        )

        env_runtime_spec.additional_properties = d
        return env_runtime_spec

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
