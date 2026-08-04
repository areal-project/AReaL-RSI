from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.benchmark_job import BenchmarkJob
    from ..models.stream_task_row_reward_type_0 import StreamTaskRowRewardType0
    from ..models.task import Task


T = TypeVar("T", bound="StreamTaskRow")


@_attrs_define
class StreamTaskRow:
    """
    Attributes:
        task (Task):
        job (BenchmarkJob | Unset):
        reward (None | StreamTaskRowRewardType0 | Unset):
        data_id (str | Unset):
        partition (str | Unset):
        experiment (str | Unset): Experiment dimension of the task; absent for tasks launched without one.
    """

    task: Task
    job: BenchmarkJob | Unset = UNSET
    reward: None | StreamTaskRowRewardType0 | Unset = UNSET
    data_id: str | Unset = UNSET
    partition: str | Unset = UNSET
    experiment: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.stream_task_row_reward_type_0 import StreamTaskRowRewardType0

        task = self.task.to_dict()

        job: dict[str, Any] | Unset = UNSET
        if not isinstance(self.job, Unset):
            job = self.job.to_dict()

        reward: dict[str, Any] | None | Unset
        if isinstance(self.reward, Unset):
            reward = UNSET
        elif isinstance(self.reward, StreamTaskRowRewardType0):
            reward = self.reward.to_dict()
        else:
            reward = self.reward

        data_id = self.data_id

        partition = self.partition

        experiment = self.experiment

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "task": task,
            }
        )
        if job is not UNSET:
            field_dict["job"] = job
        if reward is not UNSET:
            field_dict["reward"] = reward
        if data_id is not UNSET:
            field_dict["data_id"] = data_id
        if partition is not UNSET:
            field_dict["partition"] = partition
        if experiment is not UNSET:
            field_dict["experiment"] = experiment

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.benchmark_job import BenchmarkJob
        from ..models.stream_task_row_reward_type_0 import StreamTaskRowRewardType0
        from ..models.task import Task

        d = dict(src_dict)
        task = Task.from_dict(d.pop("task"))

        _job = d.pop("job", UNSET)
        job: BenchmarkJob | Unset
        if isinstance(_job, Unset):
            job = UNSET
        else:
            job = BenchmarkJob.from_dict(_job)

        def _parse_reward(data: object) -> None | StreamTaskRowRewardType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                reward_type_0 = StreamTaskRowRewardType0.from_dict(data)

                return reward_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | StreamTaskRowRewardType0 | Unset, data)

        reward = _parse_reward(d.pop("reward", UNSET))

        data_id = d.pop("data_id", UNSET)

        partition = d.pop("partition", UNSET)

        experiment = d.pop("experiment", UNSET)

        stream_task_row = cls(
            task=task,
            job=job,
            reward=reward,
            data_id=data_id,
            partition=partition,
            experiment=experiment,
        )

        stream_task_row.additional_properties = d
        return stream_task_row

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
