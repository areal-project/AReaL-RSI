from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.score_schema_normalization import ScoreSchemaNormalization

if TYPE_CHECKING:
    from ..models.score_schema_sub_scores_item import ScoreSchemaSubScoresItem


T = TypeVar("T", bound="ScoreSchema")


@_attrs_define
class ScoreSchema:
    """
    Attributes:
        normalization (ScoreSchemaNormalization):
        sub_scores (list[ScoreSchemaSubScoresItem]):
    """

    normalization: ScoreSchemaNormalization
    sub_scores: list[ScoreSchemaSubScoresItem]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        normalization = self.normalization.value

        sub_scores = []
        for sub_scores_item_data in self.sub_scores:
            sub_scores_item = sub_scores_item_data.to_dict()
            sub_scores.append(sub_scores_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "normalization": normalization,
                "sub_scores": sub_scores,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.score_schema_sub_scores_item import ScoreSchemaSubScoresItem

        d = dict(src_dict)
        normalization = ScoreSchemaNormalization(d.pop("normalization"))

        sub_scores = []
        _sub_scores = d.pop("sub_scores")
        for sub_scores_item_data in _sub_scores:
            sub_scores_item = ScoreSchemaSubScoresItem.from_dict(sub_scores_item_data)

            sub_scores.append(sub_scores_item)

        score_schema = cls(
            normalization=normalization,
            sub_scores=sub_scores,
        )

        score_schema.additional_properties = d
        return score_schema

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
