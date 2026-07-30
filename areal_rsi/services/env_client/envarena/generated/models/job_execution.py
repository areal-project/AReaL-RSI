from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.job_execution_mode import JobExecutionMode
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.job_execution_rewards_snapshot_item import (
        JobExecutionRewardsSnapshotItem,
    )
    from ..models.job_result import JobResult
    from ..models.task import Task


T = TypeVar("T", bound="JobExecution")


@_attrs_define
class JobExecution:
    """
    Attributes:
        job_id (str):
        generation (int): The archived generation number.
        mode (JobExecutionMode):
        reset_task_ids (list[str]):
        created_at (datetime.datetime):
        requested_by (str | Unset):
        task_count (int | Unset): Number of task rows in the archived snapshot.
        reward_count (int | Unset): Number of archived rewards (rewards of the reset tasks).
        result_snapshot (JobResult | Unset):
        tasks_snapshot (list[Task] | Unset): Present only with full=true.
        rewards_snapshot (list[JobExecutionRewardsSnapshotItem] | Unset): Present only with full=true.
    """

    job_id: str
    generation: int
    mode: JobExecutionMode
    reset_task_ids: list[str]
    created_at: datetime.datetime
    requested_by: str | Unset = UNSET
    task_count: int | Unset = UNSET
    reward_count: int | Unset = UNSET
    result_snapshot: JobResult | Unset = UNSET
    tasks_snapshot: list[Task] | Unset = UNSET
    rewards_snapshot: list[JobExecutionRewardsSnapshotItem] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        job_id = self.job_id

        generation = self.generation

        mode = self.mode.value

        reset_task_ids = self.reset_task_ids

        created_at = self.created_at.isoformat()

        requested_by = self.requested_by

        task_count = self.task_count

        reward_count = self.reward_count

        result_snapshot: dict[str, Any] | Unset = UNSET
        if not isinstance(self.result_snapshot, Unset):
            result_snapshot = self.result_snapshot.to_dict()

        tasks_snapshot: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.tasks_snapshot, Unset):
            tasks_snapshot = []
            for tasks_snapshot_item_data in self.tasks_snapshot:
                tasks_snapshot_item = tasks_snapshot_item_data.to_dict()
                tasks_snapshot.append(tasks_snapshot_item)

        rewards_snapshot: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.rewards_snapshot, Unset):
            rewards_snapshot = []
            for rewards_snapshot_item_data in self.rewards_snapshot:
                rewards_snapshot_item = rewards_snapshot_item_data.to_dict()
                rewards_snapshot.append(rewards_snapshot_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "job_id": job_id,
                "generation": generation,
                "mode": mode,
                "reset_task_ids": reset_task_ids,
                "created_at": created_at,
            }
        )
        if requested_by is not UNSET:
            field_dict["requested_by"] = requested_by
        if task_count is not UNSET:
            field_dict["task_count"] = task_count
        if reward_count is not UNSET:
            field_dict["reward_count"] = reward_count
        if result_snapshot is not UNSET:
            field_dict["result_snapshot"] = result_snapshot
        if tasks_snapshot is not UNSET:
            field_dict["tasks_snapshot"] = tasks_snapshot
        if rewards_snapshot is not UNSET:
            field_dict["rewards_snapshot"] = rewards_snapshot

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.job_execution_rewards_snapshot_item import (
            JobExecutionRewardsSnapshotItem,
        )
        from ..models.job_result import JobResult
        from ..models.task import Task

        d = dict(src_dict)
        job_id = d.pop("job_id")

        generation = d.pop("generation")

        mode = JobExecutionMode(d.pop("mode"))

        reset_task_ids = cast(list[str], d.pop("reset_task_ids"))

        created_at = isoparse(d.pop("created_at"))

        requested_by = d.pop("requested_by", UNSET)

        task_count = d.pop("task_count", UNSET)

        reward_count = d.pop("reward_count", UNSET)

        _result_snapshot = d.pop("result_snapshot", UNSET)
        result_snapshot: JobResult | Unset
        if isinstance(_result_snapshot, Unset):
            result_snapshot = UNSET
        else:
            result_snapshot = JobResult.from_dict(_result_snapshot)

        _tasks_snapshot = d.pop("tasks_snapshot", UNSET)
        tasks_snapshot: list[Task] | Unset = UNSET
        if _tasks_snapshot is not UNSET:
            tasks_snapshot = []
            for tasks_snapshot_item_data in _tasks_snapshot:
                tasks_snapshot_item = Task.from_dict(tasks_snapshot_item_data)

                tasks_snapshot.append(tasks_snapshot_item)

        _rewards_snapshot = d.pop("rewards_snapshot", UNSET)
        rewards_snapshot: list[JobExecutionRewardsSnapshotItem] | Unset = UNSET
        if _rewards_snapshot is not UNSET:
            rewards_snapshot = []
            for rewards_snapshot_item_data in _rewards_snapshot:
                rewards_snapshot_item = JobExecutionRewardsSnapshotItem.from_dict(
                    rewards_snapshot_item_data
                )

                rewards_snapshot.append(rewards_snapshot_item)

        job_execution = cls(
            job_id=job_id,
            generation=generation,
            mode=mode,
            reset_task_ids=reset_task_ids,
            created_at=created_at,
            requested_by=requested_by,
            task_count=task_count,
            reward_count=reward_count,
            result_snapshot=result_snapshot,
            tasks_snapshot=tasks_snapshot,
            rewards_snapshot=rewards_snapshot,
        )

        job_execution.additional_properties = d
        return job_execution

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
