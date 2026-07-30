from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.reward_review_authoritative import RewardReviewAuthoritative
from ..models.reward_review_schema import RewardReviewSchema
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.reward_review_agent import RewardReviewAgent
    from ..models.reward_review_check import RewardReviewCheck
    from ..models.reward_review_sub_scores import RewardReviewSubScores


T = TypeVar("T", bound="RewardReview")


@_attrs_define
class RewardReview:
    """
    Attributes:
        schema (RewardReviewSchema):
        authoritative (RewardReviewAuthoritative):
        status (str):
        score (float):
        pass_ (bool):
        checks (list[RewardReviewCheck]):
        sub_scores (RewardReviewSubScores | Unset):
        agent (RewardReviewAgent | Unset):
    """

    schema: RewardReviewSchema
    authoritative: RewardReviewAuthoritative
    status: str
    score: float
    pass_: bool
    checks: list[RewardReviewCheck]
    sub_scores: RewardReviewSubScores | Unset = UNSET
    agent: RewardReviewAgent | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        schema = self.schema.value

        authoritative = self.authoritative.value

        status = self.status

        score = self.score

        pass_ = self.pass_

        checks = []
        for checks_item_data in self.checks:
            checks_item = checks_item_data.to_dict()
            checks.append(checks_item)

        sub_scores: dict[str, Any] | Unset = UNSET
        if not isinstance(self.sub_scores, Unset):
            sub_scores = self.sub_scores.to_dict()

        agent: dict[str, Any] | Unset = UNSET
        if not isinstance(self.agent, Unset):
            agent = self.agent.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "schema": schema,
                "authoritative": authoritative,
                "status": status,
                "score": score,
                "pass": pass_,
                "checks": checks,
            }
        )
        if sub_scores is not UNSET:
            field_dict["sub_scores"] = sub_scores
        if agent is not UNSET:
            field_dict["agent"] = agent

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.reward_review_agent import RewardReviewAgent
        from ..models.reward_review_check import RewardReviewCheck
        from ..models.reward_review_sub_scores import RewardReviewSubScores

        d = dict(src_dict)
        schema = RewardReviewSchema(d.pop("schema"))

        authoritative = RewardReviewAuthoritative(d.pop("authoritative"))

        status = d.pop("status")

        score = d.pop("score")

        pass_ = d.pop("pass")

        checks = []
        _checks = d.pop("checks")
        for checks_item_data in _checks:
            checks_item = RewardReviewCheck.from_dict(checks_item_data)

            checks.append(checks_item)

        _sub_scores = d.pop("sub_scores", UNSET)
        sub_scores: RewardReviewSubScores | Unset
        if isinstance(_sub_scores, Unset):
            sub_scores = UNSET
        else:
            sub_scores = RewardReviewSubScores.from_dict(_sub_scores)

        _agent = d.pop("agent", UNSET)
        agent: RewardReviewAgent | Unset
        if isinstance(_agent, Unset):
            agent = UNSET
        else:
            agent = RewardReviewAgent.from_dict(_agent)

        reward_review = cls(
            schema=schema,
            authoritative=authoritative,
            status=status,
            score=score,
            pass_=pass_,
            checks=checks,
            sub_scores=sub_scores,
            agent=agent,
        )

        reward_review.additional_properties = d
        return reward_review

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
