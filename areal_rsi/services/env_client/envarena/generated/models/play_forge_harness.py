from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.play_forge_harness_envs import PlayForgeHarnessEnvs


T = TypeVar("T", bound="PlayForgeHarness")


@_attrs_define
class PlayForgeHarness:
    """
    Attributes:
        model_id (str | Unset): Non-secret sandbox model identifier. EnvArena maps it to ARCA envs.GAMEAGENT_MODEL and
            rejects a conflicting harness.envs.GAMEAGENT_MODEL value.
        system_prompt (str | Unset): Complete OSS object path inside `<scope>/prompt-catalog/` or
            `<scope>/user_prompts/`. The object must be non-empty UTF-8, use `.md` or `.txt`, be at most 32 KiB, and not
            contain template-reserved prompt terms (`sandbox`, `mount`, `oss`, or `挂载`).
        skills (list[str] | Unset): Complete OSS directories inside `<scope>/skill-catalog/` or `<scope>/user_skills/`,
            mounted read-only at their canonical GameAgent selector roots.
        envs (PlayForgeHarnessEnvs | Unset): User runtime environment values merged into the ARCA environment. EnvArena
            injects task, trace, output, trajectory, and OTel path variables after rejecting reserved platform keys.
            PlayForge does not receive EnvArena built-in OTLP exporter configuration because its template owns telemetry.
            EnvArena persists only masked user-supplied values in the PlayForge Harness snapshot.
    """

    model_id: str | Unset = UNSET
    system_prompt: str | Unset = UNSET
    skills: list[str] | Unset = UNSET
    envs: PlayForgeHarnessEnvs | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        model_id = self.model_id

        system_prompt = self.system_prompt

        skills: list[str] | Unset = UNSET
        if not isinstance(self.skills, Unset):
            skills = self.skills

        envs: dict[str, Any] | Unset = UNSET
        if not isinstance(self.envs, Unset):
            envs = self.envs.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if model_id is not UNSET:
            field_dict["model_id"] = model_id
        if system_prompt is not UNSET:
            field_dict["system_prompt"] = system_prompt
        if skills is not UNSET:
            field_dict["skills"] = skills
        if envs is not UNSET:
            field_dict["envs"] = envs

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.play_forge_harness_envs import PlayForgeHarnessEnvs

        d = dict(src_dict)
        model_id = d.pop("model_id", UNSET)

        system_prompt = d.pop("system_prompt", UNSET)

        skills = cast(list[str], d.pop("skills", UNSET))

        _envs = d.pop("envs", UNSET)
        envs: PlayForgeHarnessEnvs | Unset
        if isinstance(_envs, Unset):
            envs = UNSET
        else:
            envs = PlayForgeHarnessEnvs.from_dict(_envs)

        play_forge_harness = cls(
            model_id=model_id,
            system_prompt=system_prompt,
            skills=skills,
            envs=envs,
        )

        play_forge_harness.additional_properties = d
        return play_forge_harness

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
