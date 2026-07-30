from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.version_ref import VersionRef


T = TypeVar("T", bound="JobResultLeaderboardKey")


@_attrs_define
class JobResultLeaderboardKey:
    """
    Attributes:
        benchmark_ref (VersionRef | Unset):
        harness_ref (VersionRef | Unset):
        model_name (str | Unset):
    """

    benchmark_ref: VersionRef | Unset = UNSET
    harness_ref: VersionRef | Unset = UNSET
    model_name: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        benchmark_ref: dict[str, Any] | Unset = UNSET
        if not isinstance(self.benchmark_ref, Unset):
            benchmark_ref = self.benchmark_ref.to_dict()

        harness_ref: dict[str, Any] | Unset = UNSET
        if not isinstance(self.harness_ref, Unset):
            harness_ref = self.harness_ref.to_dict()

        model_name = self.model_name

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if benchmark_ref is not UNSET:
            field_dict["benchmark_ref"] = benchmark_ref
        if harness_ref is not UNSET:
            field_dict["harness_ref"] = harness_ref
        if model_name is not UNSET:
            field_dict["model_name"] = model_name

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.version_ref import VersionRef

        d = dict(src_dict)
        _benchmark_ref = d.pop("benchmark_ref", UNSET)
        benchmark_ref: VersionRef | Unset
        if isinstance(_benchmark_ref, Unset):
            benchmark_ref = UNSET
        else:
            benchmark_ref = VersionRef.from_dict(_benchmark_ref)

        _harness_ref = d.pop("harness_ref", UNSET)
        harness_ref: VersionRef | Unset
        if isinstance(_harness_ref, Unset):
            harness_ref = UNSET
        else:
            harness_ref = VersionRef.from_dict(_harness_ref)

        model_name = d.pop("model_name", UNSET)

        job_result_leaderboard_key = cls(
            benchmark_ref=benchmark_ref,
            harness_ref=harness_ref,
            model_name=model_name,
        )

        job_result_leaderboard_key.additional_properties = d
        return job_result_leaderboard_key

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
