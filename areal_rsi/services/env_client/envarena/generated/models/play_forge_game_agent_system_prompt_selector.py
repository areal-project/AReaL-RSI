from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.play_forge_game_agent_system_prompt_selector_source import (
    PlayForgeGameAgentSystemPromptSelectorSource,
)

T = TypeVar("T", bound="PlayForgeGameAgentSystemPromptSelector")


@_attrs_define
class PlayForgeGameAgentSystemPromptSelector:
    """
    Attributes:
        source (PlayForgeGameAgentSystemPromptSelectorSource):
        path (str):
    """

    source: PlayForgeGameAgentSystemPromptSelectorSource
    path: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        source = self.source.value

        path = self.path

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "source": source,
                "path": path,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        source = PlayForgeGameAgentSystemPromptSelectorSource(d.pop("source"))

        path = d.pop("path")

        play_forge_game_agent_system_prompt_selector = cls(
            source=source,
            path=path,
        )

        play_forge_game_agent_system_prompt_selector.additional_properties = d
        return play_forge_game_agent_system_prompt_selector

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
