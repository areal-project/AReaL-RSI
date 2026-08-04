from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.play_forge_game_agent_skill_selector_source import (
    PlayForgeGameAgentSkillSelectorSource,
)

T = TypeVar("T", bound="PlayForgeGameAgentSkillSelector")


@_attrs_define
class PlayForgeGameAgentSkillSelector:
    """
    Attributes:
        source (PlayForgeGameAgentSkillSelectorSource):
        name (str):
    """

    source: PlayForgeGameAgentSkillSelectorSource
    name: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        source = self.source.value

        name = self.name

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "source": source,
                "name": name,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        source = PlayForgeGameAgentSkillSelectorSource(d.pop("source"))

        name = d.pop("name")

        play_forge_game_agent_skill_selector = cls(
            source=source,
            name=name,
        )

        play_forge_game_agent_skill_selector.additional_properties = d
        return play_forge_game_agent_skill_selector

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
