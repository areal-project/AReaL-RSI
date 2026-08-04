from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.play_forge_game_agent_skill_selector import (
        PlayForgeGameAgentSkillSelector,
    )
    from ..models.play_forge_game_agent_system_prompt_selector import (
        PlayForgeGameAgentSystemPromptSelector,
    )


T = TypeVar("T", bound="PlayForgeGameAgentSelector")


@_attrs_define
class PlayForgeGameAgentSelector:
    """
    Attributes:
        model_id (str | Unset):
        system_prompt (PlayForgeGameAgentSystemPromptSelector | Unset):
        skills (list[PlayForgeGameAgentSkillSelector] | Unset):
    """

    model_id: str | Unset = UNSET
    system_prompt: PlayForgeGameAgentSystemPromptSelector | Unset = UNSET
    skills: list[PlayForgeGameAgentSkillSelector] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        model_id = self.model_id

        system_prompt: dict[str, Any] | Unset = UNSET
        if not isinstance(self.system_prompt, Unset):
            system_prompt = self.system_prompt.to_dict()

        skills: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.skills, Unset):
            skills = []
            for skills_item_data in self.skills:
                skills_item = skills_item_data.to_dict()
                skills.append(skills_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if model_id is not UNSET:
            field_dict["model_id"] = model_id
        if system_prompt is not UNSET:
            field_dict["system_prompt"] = system_prompt
        if skills is not UNSET:
            field_dict["skills"] = skills

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.play_forge_game_agent_skill_selector import (
            PlayForgeGameAgentSkillSelector,
        )
        from ..models.play_forge_game_agent_system_prompt_selector import (
            PlayForgeGameAgentSystemPromptSelector,
        )

        d = dict(src_dict)
        model_id = d.pop("model_id", UNSET)

        _system_prompt = d.pop("system_prompt", UNSET)
        system_prompt: PlayForgeGameAgentSystemPromptSelector | Unset
        if isinstance(_system_prompt, Unset):
            system_prompt = UNSET
        else:
            system_prompt = PlayForgeGameAgentSystemPromptSelector.from_dict(
                _system_prompt
            )

        _skills = d.pop("skills", UNSET)
        skills: list[PlayForgeGameAgentSkillSelector] | Unset = UNSET
        if _skills is not UNSET:
            skills = []
            for skills_item_data in _skills:
                skills_item = PlayForgeGameAgentSkillSelector.from_dict(
                    skills_item_data
                )

                skills.append(skills_item)

        play_forge_game_agent_selector = cls(
            model_id=model_id,
            system_prompt=system_prompt,
            skills=skills,
        )

        play_forge_game_agent_selector.additional_properties = d
        return play_forge_game_agent_selector

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
