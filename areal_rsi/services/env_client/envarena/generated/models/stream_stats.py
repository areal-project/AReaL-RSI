from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.stream_reward_point import StreamRewardPoint


T = TypeVar("T", bound="StreamStats")


@_attrs_define
class StreamStats:
    """
    Attributes:
        request_count (int):
        pending_count (int):
        running_count (int):
        done_count (int):
        failed_count (int):
        reward_points (list[StreamRewardPoint]): The 1,000 most recently computed successful reward points, ordered by
            computed_at.
        experiment (str): The experiment scope these counts belong to. Empty means the unlabeled tasks.
        all_experiments (bool):
        avg_reward (float | None | Unset):
    """

    request_count: int
    pending_count: int
    running_count: int
    done_count: int
    failed_count: int
    reward_points: list[StreamRewardPoint]
    experiment: str
    all_experiments: bool
    avg_reward: float | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        request_count = self.request_count

        pending_count = self.pending_count

        running_count = self.running_count

        done_count = self.done_count

        failed_count = self.failed_count

        reward_points = []
        for reward_points_item_data in self.reward_points:
            reward_points_item = reward_points_item_data.to_dict()
            reward_points.append(reward_points_item)

        experiment = self.experiment

        all_experiments = self.all_experiments

        avg_reward: float | None | Unset
        if isinstance(self.avg_reward, Unset):
            avg_reward = UNSET
        else:
            avg_reward = self.avg_reward

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "request_count": request_count,
                "pending_count": pending_count,
                "running_count": running_count,
                "done_count": done_count,
                "failed_count": failed_count,
                "reward_points": reward_points,
                "experiment": experiment,
                "all_experiments": all_experiments,
            }
        )
        if avg_reward is not UNSET:
            field_dict["avg_reward"] = avg_reward

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.stream_reward_point import StreamRewardPoint

        d = dict(src_dict)
        request_count = d.pop("request_count")

        pending_count = d.pop("pending_count")

        running_count = d.pop("running_count")

        done_count = d.pop("done_count")

        failed_count = d.pop("failed_count")

        reward_points = []
        _reward_points = d.pop("reward_points")
        for reward_points_item_data in _reward_points:
            reward_points_item = StreamRewardPoint.from_dict(reward_points_item_data)

            reward_points.append(reward_points_item)

        experiment = d.pop("experiment")

        all_experiments = d.pop("all_experiments")

        def _parse_avg_reward(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        avg_reward = _parse_avg_reward(d.pop("avg_reward", UNSET))

        stream_stats = cls(
            request_count=request_count,
            pending_count=pending_count,
            running_count=running_count,
            done_count=done_count,
            failed_count=failed_count,
            reward_points=reward_points,
            experiment=experiment,
            all_experiments=all_experiments,
            avg_reward=avg_reward,
        )

        stream_stats.additional_properties = d
        return stream_stats

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
