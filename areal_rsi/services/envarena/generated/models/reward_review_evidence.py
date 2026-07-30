from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.reward_review_evidence_kind import RewardReviewEvidenceKind
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.reward_review_evidence_asset import RewardReviewEvidenceAsset


T = TypeVar("T", bound="RewardReviewEvidence")


@_attrs_define
class RewardReviewEvidence:
    """
    Attributes:
        id (str):
        kind (RewardReviewEvidenceKind):
        detail (str):
        artifact (RewardReviewEvidenceAsset | Unset):
    """

    id: str
    kind: RewardReviewEvidenceKind
    detail: str
    artifact: RewardReviewEvidenceAsset | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        kind = self.kind.value

        detail = self.detail

        artifact: dict[str, Any] | Unset = UNSET
        if not isinstance(self.artifact, Unset):
            artifact = self.artifact.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "kind": kind,
                "detail": detail,
            }
        )
        if artifact is not UNSET:
            field_dict["artifact"] = artifact

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.reward_review_evidence_asset import RewardReviewEvidenceAsset

        d = dict(src_dict)
        id = d.pop("id")

        kind = RewardReviewEvidenceKind(d.pop("kind"))

        detail = d.pop("detail")

        _artifact = d.pop("artifact", UNSET)
        artifact: RewardReviewEvidenceAsset | Unset
        if isinstance(_artifact, Unset):
            artifact = UNSET
        else:
            artifact = RewardReviewEvidenceAsset.from_dict(_artifact)

        reward_review_evidence = cls(
            id=id,
            kind=kind,
            detail=detail,
            artifact=artifact,
        )

        reward_review_evidence.additional_properties = d
        return reward_review_evidence

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
