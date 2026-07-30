from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.arena_matrix_issue import ArenaMatrixIssue
    from ..models.version_ref import VersionRef


T = TypeVar("T", bound="ArenaMatrixRow")


@_attrs_define
class ArenaMatrixRow:
    """
    Attributes:
        id (str):
        ability (str):
        benchmark_refs (list[VersionRef]):
        raw_benchmark_names (list[str] | Unset):
        issues (list[ArenaMatrixIssue] | Unset):
    """

    id: str
    ability: str
    benchmark_refs: list[VersionRef]
    raw_benchmark_names: list[str] | Unset = UNSET
    issues: list[ArenaMatrixIssue] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        ability = self.ability

        benchmark_refs = []
        for benchmark_refs_item_data in self.benchmark_refs:
            benchmark_refs_item = benchmark_refs_item_data.to_dict()
            benchmark_refs.append(benchmark_refs_item)

        raw_benchmark_names: list[str] | Unset = UNSET
        if not isinstance(self.raw_benchmark_names, Unset):
            raw_benchmark_names = self.raw_benchmark_names

        issues: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.issues, Unset):
            issues = []
            for issues_item_data in self.issues:
                issues_item = issues_item_data.to_dict()
                issues.append(issues_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "ability": ability,
                "benchmark_refs": benchmark_refs,
            }
        )
        if raw_benchmark_names is not UNSET:
            field_dict["raw_benchmark_names"] = raw_benchmark_names
        if issues is not UNSET:
            field_dict["issues"] = issues

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.arena_matrix_issue import ArenaMatrixIssue
        from ..models.version_ref import VersionRef

        d = dict(src_dict)
        id = d.pop("id")

        ability = d.pop("ability")

        benchmark_refs = []
        _benchmark_refs = d.pop("benchmark_refs")
        for benchmark_refs_item_data in _benchmark_refs:
            benchmark_refs_item = VersionRef.from_dict(benchmark_refs_item_data)

            benchmark_refs.append(benchmark_refs_item)

        raw_benchmark_names = cast(list[str], d.pop("raw_benchmark_names", UNSET))

        _issues = d.pop("issues", UNSET)
        issues: list[ArenaMatrixIssue] | Unset = UNSET
        if _issues is not UNSET:
            issues = []
            for issues_item_data in _issues:
                issues_item = ArenaMatrixIssue.from_dict(issues_item_data)

                issues.append(issues_item)

        arena_matrix_row = cls(
            id=id,
            ability=ability,
            benchmark_refs=benchmark_refs,
            raw_benchmark_names=raw_benchmark_names,
            issues=issues,
        )

        arena_matrix_row.additional_properties = d
        return arena_matrix_row

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
