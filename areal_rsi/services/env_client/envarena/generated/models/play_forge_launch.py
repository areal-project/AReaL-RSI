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
            harness (PlayForgeHarness):
            output_oss_path (str): PlayForge release OSS path mounted read-write at `/output`. It must be below
                ARENA_PLAYFORGE_OUTPUT_PREFIX.
            sandbox_ttl_minutes (int | Unset): Optional runtime TTL. When absent, EnvArena uses
                ARENA_PLAYFORGE_SANDBOX_TTL_MINUTES (default 60).
            mounts (list[PlayForgeMount] | Unset):
            metadata (PlayForgeLaunchMetadata | Unset):
    """

    runtime_profile: PlayForgeLaunchRuntimeProfile
    harness: PlayForgeHarness
    output_oss_path: str
    sandbox_ttl_minutes: int | Unset = UNSET
    mounts: list[PlayForgeMount] | Unset = UNSET
    metadata: PlayForgeLaunchMetadata | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        runtime_profile = self.runtime_profile.value

        harness = self.harness.to_dict()

        output_oss_path = self.output_oss_path

        sandbox_ttl_minutes = self.sandbox_ttl_minutes

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
                "harness": harness,
                "output_oss_path": output_oss_path,
            }
        )
        if sandbox_ttl_minutes is not UNSET:
            field_dict["sandbox_ttl_minutes"] = sandbox_ttl_minutes
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

        harness = PlayForgeHarness.from_dict(d.pop("harness"))

        output_oss_path = d.pop("output_oss_path")

        sandbox_ttl_minutes = d.pop("sandbox_ttl_minutes", UNSET)

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
            harness=harness,
            output_oss_path=output_oss_path,
            sandbox_ttl_minutes=sandbox_ttl_minutes,
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
