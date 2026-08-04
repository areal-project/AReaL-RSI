from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.play_forge_game_agent_selector import PlayForgeGameAgentSelector
    from ..models.play_forge_runtime_mount import PlayForgeRuntimeMount


T = TypeVar("T", bound="PlayForgeRuntimeSnapshot")


@_attrs_define
class PlayForgeRuntimeSnapshot:
    """
    Attributes:
        runtime_profile (str):
        template_id (str):
        ttl_minutes (int):
        release_output (PlayForgeRuntimeMount):
        telemetry_output (PlayForgeRuntimeMount):
        gameagent (PlayForgeGameAgentSelector):
    """

    runtime_profile: str
    template_id: str
    ttl_minutes: int
    release_output: PlayForgeRuntimeMount
    telemetry_output: PlayForgeRuntimeMount
    gameagent: PlayForgeGameAgentSelector
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        runtime_profile = self.runtime_profile

        template_id = self.template_id

        ttl_minutes = self.ttl_minutes

        release_output = self.release_output.to_dict()

        telemetry_output = self.telemetry_output.to_dict()

        gameagent = self.gameagent.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "runtime_profile": runtime_profile,
                "template_id": template_id,
                "ttl_minutes": ttl_minutes,
                "release_output": release_output,
                "telemetry_output": telemetry_output,
                "gameagent": gameagent,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.play_forge_game_agent_selector import PlayForgeGameAgentSelector
        from ..models.play_forge_runtime_mount import PlayForgeRuntimeMount

        d = dict(src_dict)
        runtime_profile = d.pop("runtime_profile")

        template_id = d.pop("template_id")

        ttl_minutes = d.pop("ttl_minutes")

        release_output = PlayForgeRuntimeMount.from_dict(d.pop("release_output"))

        telemetry_output = PlayForgeRuntimeMount.from_dict(d.pop("telemetry_output"))

        gameagent = PlayForgeGameAgentSelector.from_dict(d.pop("gameagent"))

        play_forge_runtime_snapshot = cls(
            runtime_profile=runtime_profile,
            template_id=template_id,
            ttl_minutes=ttl_minutes,
            release_output=release_output,
            telemetry_output=telemetry_output,
            gameagent=gameagent,
        )

        play_forge_runtime_snapshot.additional_properties = d
        return play_forge_runtime_snapshot

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
