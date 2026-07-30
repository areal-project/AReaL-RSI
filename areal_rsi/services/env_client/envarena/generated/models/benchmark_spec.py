from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.aggregation_spec import AggregationSpec
    from ..models.benchmark_env_ref import BenchmarkEnvRef
    from ..models.benchmark_spec_metadata import BenchmarkSpecMetadata
    from ..models.version_ref import VersionRef


T = TypeVar("T", bound="BenchmarkSpec")


@_attrs_define
class BenchmarkSpec:
    """Benchmark binds one published Reward to one or more published Envs. When both sides declare oracle schemas, Reward
    accepted_oracle_schema must match each Env oracle_schema at publish/job creation time.

        Attributes:
            reward_ref (VersionRef):
            env_refs (list[BenchmarkEnvRef]):
            aggregation (AggregationSpec):
            tags (list[str] | Unset):
            metadata (BenchmarkSpecMetadata | Unset):
    """

    reward_ref: VersionRef
    env_refs: list[BenchmarkEnvRef]
    aggregation: AggregationSpec
    tags: list[str] | Unset = UNSET
    metadata: BenchmarkSpecMetadata | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        reward_ref = self.reward_ref.to_dict()

        env_refs = []
        for env_refs_item_data in self.env_refs:
            env_refs_item = env_refs_item_data.to_dict()
            env_refs.append(env_refs_item)

        aggregation = self.aggregation.to_dict()

        tags: list[str] | Unset = UNSET
        if not isinstance(self.tags, Unset):
            tags = self.tags

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "reward_ref": reward_ref,
                "env_refs": env_refs,
                "aggregation": aggregation,
            }
        )
        if tags is not UNSET:
            field_dict["tags"] = tags
        if metadata is not UNSET:
            field_dict["metadata"] = metadata

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.aggregation_spec import AggregationSpec
        from ..models.benchmark_env_ref import BenchmarkEnvRef
        from ..models.benchmark_spec_metadata import BenchmarkSpecMetadata
        from ..models.version_ref import VersionRef

        d = dict(src_dict)
        reward_ref = VersionRef.from_dict(d.pop("reward_ref"))

        env_refs = []
        _env_refs = d.pop("env_refs")
        for env_refs_item_data in _env_refs:
            env_refs_item = BenchmarkEnvRef.from_dict(env_refs_item_data)

            env_refs.append(env_refs_item)

        aggregation = AggregationSpec.from_dict(d.pop("aggregation"))

        tags = cast(list[str], d.pop("tags", UNSET))

        _metadata = d.pop("metadata", UNSET)
        metadata: BenchmarkSpecMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = BenchmarkSpecMetadata.from_dict(_metadata)

        benchmark_spec = cls(
            reward_ref=reward_ref,
            env_refs=env_refs,
            aggregation=aggregation,
            tags=tags,
            metadata=metadata,
        )

        benchmark_spec.additional_properties = d
        return benchmark_spec

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
