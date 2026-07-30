from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ScoreSchemaSubScoresItem")


@_attrs_define
class ScoreSchemaSubScoresItem:
    """
    Attributes:
        key (str):
        min_ (float):
        max_ (float):
        name (str | Unset):
        required (bool | Unset):  Default: True.
        weight (float | Unset):  Default: 1.0.
    """

    key: str
    min_: float
    max_: float
    name: str | Unset = UNSET
    required: bool | Unset = True
    weight: float | Unset = 1.0
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        key = self.key

        min_ = self.min_

        max_ = self.max_

        name = self.name

        required = self.required

        weight = self.weight

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "key": key,
                "min": min_,
                "max": max_,
            }
        )
        if name is not UNSET:
            field_dict["name"] = name
        if required is not UNSET:
            field_dict["required"] = required
        if weight is not UNSET:
            field_dict["weight"] = weight

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        key = d.pop("key")

        min_ = d.pop("min")

        max_ = d.pop("max")

        name = d.pop("name", UNSET)

        required = d.pop("required", UNSET)

        weight = d.pop("weight", UNSET)

        score_schema_sub_scores_item = cls(
            key=key,
            min_=min_,
            max_=max_,
            name=name,
            required=required,
            weight=weight,
        )

        score_schema_sub_scores_item.additional_properties = d
        return score_schema_sub_scores_item

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
