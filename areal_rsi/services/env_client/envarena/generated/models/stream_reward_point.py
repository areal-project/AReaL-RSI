from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

T = TypeVar("T", bound="StreamRewardPoint")


@_attrs_define
class StreamRewardPoint:
    """
    Attributes:
        task_id (str):
        reward (float):
        computed_at (datetime.datetime):
        data_id (str | Unset):
    """

    task_id: str
    reward: float
    computed_at: datetime.datetime
    data_id: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        task_id = self.task_id

        reward = self.reward

        computed_at = self.computed_at.isoformat()

        data_id = self.data_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "task_id": task_id,
                "reward": reward,
                "computed_at": computed_at,
            }
        )
        if data_id is not UNSET:
            field_dict["data_id"] = data_id

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        task_id = d.pop("task_id")

        reward = d.pop("reward")

        computed_at = isoparse(d.pop("computed_at"))

        data_id = d.pop("data_id", UNSET)

        stream_reward_point = cls(
            task_id=task_id,
            reward=reward,
            computed_at=computed_at,
            data_id=data_id,
        )

        stream_reward_point.additional_properties = d
        return stream_reward_point

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
