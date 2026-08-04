from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.play_forge_harness_snapshot_envs import PlayForgeHarnessSnapshotEnvs
    from ..models.play_forge_mount import PlayForgeMount
    from ..models.play_forge_runtime_snapshot import PlayForgeRuntimeSnapshot


T = TypeVar("T", bound="PlayForgeHarnessSnapshot")


@_attrs_define
class PlayForgeHarnessSnapshot:
    """
    Attributes:
        model_id (str | Unset): Non-secret model identifier selected for the ARCA sandbox.
        system_prompt (PlayForgeMount | Unset):
        skills (list[PlayForgeMount] | Unset):
        envs (PlayForgeHarnessSnapshotEnvs | Unset): Redacted Harness environment values retained for task audit. Never
            use this field for secrets.
        runtime (PlayForgeRuntimeSnapshot | Unset):
    """

    model_id: str | Unset = UNSET
    system_prompt: PlayForgeMount | Unset = UNSET
    skills: list[PlayForgeMount] | Unset = UNSET
    envs: PlayForgeHarnessSnapshotEnvs | Unset = UNSET
    runtime: PlayForgeRuntimeSnapshot | Unset = UNSET
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

        envs: dict[str, Any] | Unset = UNSET
        if not isinstance(self.envs, Unset):
            envs = self.envs.to_dict()

        runtime: dict[str, Any] | Unset = UNSET
        if not isinstance(self.runtime, Unset):
            runtime = self.runtime.to_dict()

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
        if runtime is not UNSET:
            field_dict["runtime"] = runtime

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.play_forge_harness_snapshot_envs import (
            PlayForgeHarnessSnapshotEnvs,
        )
        from ..models.play_forge_mount import PlayForgeMount
        from ..models.play_forge_runtime_snapshot import PlayForgeRuntimeSnapshot

        d = dict(src_dict)
        model_id = d.pop("model_id", UNSET)

        _system_prompt = d.pop("system_prompt", UNSET)
        system_prompt: PlayForgeMount | Unset
        if isinstance(_system_prompt, Unset):
            system_prompt = UNSET
        else:
            system_prompt = PlayForgeMount.from_dict(_system_prompt)

        _skills = d.pop("skills", UNSET)
        skills: list[PlayForgeMount] | Unset = UNSET
        if _skills is not UNSET:
            skills = []
            for skills_item_data in _skills:
                skills_item = PlayForgeMount.from_dict(skills_item_data)

                skills.append(skills_item)

        _envs = d.pop("envs", UNSET)
        envs: PlayForgeHarnessSnapshotEnvs | Unset
        if isinstance(_envs, Unset):
            envs = UNSET
        else:
            envs = PlayForgeHarnessSnapshotEnvs.from_dict(_envs)

        _runtime = d.pop("runtime", UNSET)
        runtime: PlayForgeRuntimeSnapshot | Unset
        if isinstance(_runtime, Unset):
            runtime = UNSET
        else:
            runtime = PlayForgeRuntimeSnapshot.from_dict(_runtime)

        play_forge_harness_snapshot = cls(
            model_id=model_id,
            system_prompt=system_prompt,
            skills=skills,
            envs=envs,
            runtime=runtime,
        )

        play_forge_harness_snapshot.additional_properties = d
        return play_forge_harness_snapshot

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
