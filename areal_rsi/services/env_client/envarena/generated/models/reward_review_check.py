from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.reward_review_check_gate import RewardReviewCheckGate
from ..models.reward_review_check_status import RewardReviewCheckStatus
from ..models.reward_review_check_visibility import RewardReviewCheckVisibility
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.reward_review_evidence import RewardReviewEvidence


T = TypeVar("T", bound="RewardReviewCheck")


@_attrs_define
class RewardReviewCheck:
    """
    Attributes:
        id (str):
        label (str): Stable check label. Hidden criteria are redacted for non-root callers.
        visibility (RewardReviewCheckVisibility):
        gate (RewardReviewCheckGate):
        required (bool):
        status (RewardReviewCheckStatus):
        score (float): Binary pass result for this atomic check.
        kind (str | Unset):
        weight (float | Unset):
        contribution (float | Unset): Earned weighted contribution. P0 checks are gates and have no contribution.
        detail (str | Unset):
        evidence (list[RewardReviewEvidence] | Unset):
    """

    id: str
    label: str
    visibility: RewardReviewCheckVisibility
    gate: RewardReviewCheckGate
    required: bool
    status: RewardReviewCheckStatus
    score: float
    kind: str | Unset = UNSET
    weight: float | Unset = UNSET
    contribution: float | Unset = UNSET
    detail: str | Unset = UNSET
    evidence: list[RewardReviewEvidence] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        label = self.label

        visibility = self.visibility.value

        gate = self.gate.value

        required = self.required

        status = self.status.value

        score = self.score

        kind = self.kind

        weight = self.weight

        contribution = self.contribution

        detail = self.detail

        evidence: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.evidence, Unset):
            evidence = []
            for evidence_item_data in self.evidence:
                evidence_item = evidence_item_data.to_dict()
                evidence.append(evidence_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "label": label,
                "visibility": visibility,
                "gate": gate,
                "required": required,
                "status": status,
                "score": score,
            }
        )
        if kind is not UNSET:
            field_dict["kind"] = kind
        if weight is not UNSET:
            field_dict["weight"] = weight
        if contribution is not UNSET:
            field_dict["contribution"] = contribution
        if detail is not UNSET:
            field_dict["detail"] = detail
        if evidence is not UNSET:
            field_dict["evidence"] = evidence

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.reward_review_evidence import RewardReviewEvidence

        d = dict(src_dict)
        id = d.pop("id")

        label = d.pop("label")

        visibility = RewardReviewCheckVisibility(d.pop("visibility"))

        gate = RewardReviewCheckGate(d.pop("gate"))

        required = d.pop("required")

        status = RewardReviewCheckStatus(d.pop("status"))

        score = d.pop("score")

        kind = d.pop("kind", UNSET)

        weight = d.pop("weight", UNSET)

        contribution = d.pop("contribution", UNSET)

        detail = d.pop("detail", UNSET)

        _evidence = d.pop("evidence", UNSET)
        evidence: list[RewardReviewEvidence] | Unset = UNSET
        if _evidence is not UNSET:
            evidence = []
            for evidence_item_data in _evidence:
                evidence_item = RewardReviewEvidence.from_dict(evidence_item_data)

                evidence.append(evidence_item)

        reward_review_check = cls(
            id=id,
            label=label,
            visibility=visibility,
            gate=gate,
            required=required,
            status=status,
            score=score,
            kind=kind,
            weight=weight,
            contribution=contribution,
            detail=detail,
            evidence=evidence,
        )

        reward_review_check.additional_properties = d
        return reward_review_check

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
