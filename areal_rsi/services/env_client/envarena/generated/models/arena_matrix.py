from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.arena_matrix_stage import ArenaMatrixStage


T = TypeVar("T", bound="ArenaMatrix")


@_attrs_define
class ArenaMatrix:
    """
    Attributes:
        stages (list[ArenaMatrixStage]):
        version (int | Unset): Schema version; server coerces to >= 1.
    """

    stages: list[ArenaMatrixStage]
    version: int | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        stages = []
        for stages_item_data in self.stages:
            stages_item = stages_item_data.to_dict()
            stages.append(stages_item)

        version = self.version

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "stages": stages,
            }
        )
        if version is not UNSET:
            field_dict["version"] = version

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.arena_matrix_stage import ArenaMatrixStage

        d = dict(src_dict)
        stages = []
        _stages = d.pop("stages")
        for stages_item_data in _stages:
            stages_item = ArenaMatrixStage.from_dict(stages_item_data)

            stages.append(stages_item)

        version = d.pop("version", UNSET)

        arena_matrix = cls(
            stages=stages,
            version=version,
        )

        arena_matrix.additional_properties = d
        return arena_matrix

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
