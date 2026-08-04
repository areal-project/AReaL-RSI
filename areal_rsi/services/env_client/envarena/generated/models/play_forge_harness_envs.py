from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="PlayForgeHarnessEnvs")


@_attrs_define
class PlayForgeHarnessEnvs:
    """User runtime environment values merged into the ARCA environment. EnvArena injects task, trace, output, trajectory,
    and OTel path variables after rejecting reserved platform keys. PlayForge does not receive EnvArena built-in OTLP
    exporter configuration because its template owns telemetry. EnvArena persists only masked user-supplied values in
    the PlayForge Harness snapshot.

    """

    additional_properties: dict[str, str] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        play_forge_harness_envs = cls()

        play_forge_harness_envs.additional_properties = d
        return play_forge_harness_envs

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> str:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: str) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
