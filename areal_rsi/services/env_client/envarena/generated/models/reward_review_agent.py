from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="RewardReviewAgent")


@_attrs_define
class RewardReviewAgent:
    """
    Attributes:
        status (str):
        advisory (bool): Always true: verifier-agent output never changes score or pass.
        comparable_check_count (int | Unset):
    """

    status: str
    advisory: bool
    comparable_check_count: int | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        status = self.status

        advisory = self.advisory

        comparable_check_count = self.comparable_check_count

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "status": status,
                "advisory": advisory,
            }
        )
        if comparable_check_count is not UNSET:
            field_dict["comparable_check_count"] = comparable_check_count

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        status = d.pop("status")

        advisory = d.pop("advisory")

        comparable_check_count = d.pop("comparable_check_count", UNSET)

        reward_review_agent = cls(
            status=status,
            advisory=advisory,
            comparable_check_count=comparable_check_count,
        )

        reward_review_agent.additional_properties = d
        return reward_review_agent

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
