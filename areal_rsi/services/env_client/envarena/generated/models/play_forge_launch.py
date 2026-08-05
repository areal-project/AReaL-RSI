from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.play_forge_launch_runtime_profile import PlayForgeLaunchRuntimeProfile
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.play_forge_harness import PlayForgeHarness
    from ..models.play_forge_launch_metadata import PlayForgeLaunchMetadata
    from ..models.play_forge_mount import PlayForgeMount


T = TypeVar("T", bound="PlayForgeLaunch")


@_attrs_define
class PlayForgeLaunch:
    """PlayForge inline Harness and controlled output contract. EnvArena resolves OSS storage and the ARCA template from
    `runtime_profile`; caller-selected network, storage, and template fields are not part of this schema. `mounts` are
    read-only additions inside the configured PlayForge OSS scope. EnvArena creates the fixed writable mounts `/output`
    and `/envarena/out`.

        Attributes:
            runtime_profile (PlayForgeLaunchRuntimeProfile): Must be `playforge`; EnvArena maps this profile to
                ARCA_PLAYFORGE_TEMPLATE_ID and ARENA_PLAYFORGE_STORAGE_REF.
            output_oss_path (str): PlayForge release OSS path mounted read-write at `/output`. It must be below
                ARENA_PLAYFORGE_OUTPUT_PREFIX.
            model_id (str | Unset): Optional model override used only when `harness` is absent or empty and EnvArena selects
                its configured default harness. An explicit non-empty `harness` controls its own `model_id` and does not inherit
                defaults.
            sandbox_ttl_minutes (int | Unset): Optional runtime TTL. When absent, EnvArena uses
                ARENA_PLAYFORGE_SANDBOX_TTL_MINUTES (default 60).
            harness (PlayForgeHarness | Unset):
            mounts (list[PlayForgeMount] | Unset):
            metadata (PlayForgeLaunchMetadata | Unset):
    """

    runtime_profile: PlayForgeLaunchRuntimeProfile
    output_oss_path: str
    model_id: str | Unset = UNSET
    sandbox_ttl_minutes: int | Unset = UNSET
    harness: PlayForgeHarness | Unset = UNSET
    mounts: list[PlayForgeMount] | Unset = UNSET
    metadata: PlayForgeLaunchMetadata | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        runtime_profile = self.runtime_profile.value

        output_oss_path = self.output_oss_path

        model_id = self.model_id

        sandbox_ttl_minutes = self.sandbox_ttl_minutes

        harness: dict[str, Any] | Unset = UNSET
        if not isinstance(self.harness, Unset):
            harness = self.harness.to_dict()

        mounts: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.mounts, Unset):
            mounts = []
            for mounts_item_data in self.mounts:
                mounts_item = mounts_item_data.to_dict()
                mounts.append(mounts_item)

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "runtime_profile": runtime_profile,
                "output_oss_path": output_oss_path,
            }
        )
        if model_id is not UNSET:
            field_dict["model_id"] = model_id
        if sandbox_ttl_minutes is not UNSET:
            field_dict["sandbox_ttl_minutes"] = sandbox_ttl_minutes
        if harness is not UNSET:
            field_dict["harness"] = harness
        if mounts is not UNSET:
            field_dict["mounts"] = mounts
        if metadata is not UNSET:
            field_dict["metadata"] = metadata

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.play_forge_harness import PlayForgeHarness
        from ..models.play_forge_launch_metadata import PlayForgeLaunchMetadata
        from ..models.play_forge_mount import PlayForgeMount

        d = dict(src_dict)
        runtime_profile = PlayForgeLaunchRuntimeProfile(d.pop("runtime_profile"))

        output_oss_path = d.pop("output_oss_path")

        model_id = d.pop("model_id", UNSET)

        sandbox_ttl_minutes = d.pop("sandbox_ttl_minutes", UNSET)

        _harness = d.pop("harness", UNSET)
        harness: PlayForgeHarness | Unset
        if isinstance(_harness, Unset):
            harness = UNSET
        else:
            harness = PlayForgeHarness.from_dict(_harness)

        _mounts = d.pop("mounts", UNSET)
        mounts: list[PlayForgeMount] | Unset = UNSET
        if _mounts is not UNSET:
            mounts = []
            for mounts_item_data in _mounts:
                mounts_item = PlayForgeMount.from_dict(mounts_item_data)

                mounts.append(mounts_item)

        _metadata = d.pop("metadata", UNSET)
        metadata: PlayForgeLaunchMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = PlayForgeLaunchMetadata.from_dict(_metadata)

        play_forge_launch = cls(
            runtime_profile=runtime_profile,
            output_oss_path=output_oss_path,
            model_id=model_id,
            sandbox_ttl_minutes=sandbox_ttl_minutes,
            harness=harness,
            mounts=mounts,
            metadata=metadata,
        )

        play_forge_launch.additional_properties = d
        return play_forge_launch

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
