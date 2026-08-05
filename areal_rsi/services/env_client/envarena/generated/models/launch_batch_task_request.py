from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.concurrency_policy import ConcurrencyPolicy
    from ..models.launch_batch_task_item import LaunchBatchTaskItem
    from ..models.launch_batch_task_request_metadata import (
        LaunchBatchTaskRequestMetadata,
    )
    from ..models.version_ref import VersionRef


T = TypeVar("T", bound="LaunchBatchTaskRequest")


@_attrs_define
class LaunchBatchTaskRequest:
    """
    Attributes:
        model_ref (VersionRef):
        tasks (list[LaunchBatchTaskItem]):
        job_id (str | Unset): Optional explicit Job ID. Omit it to generate a collision-free online Job ID.
        harness_ref (VersionRef | Unset):
        reward_ref (VersionRef | Unset):
        concurrency_policy (ConcurrencyPolicy | Unset):
        metadata (LaunchBatchTaskRequestMetadata | Unset):
        experiment (str | Unset): Optional human grouping label; job_id is the concrete batch evaluation identity.
    """

    model_ref: VersionRef
    tasks: list[LaunchBatchTaskItem]
    job_id: str | Unset = UNSET
    harness_ref: VersionRef | Unset = UNSET
    reward_ref: VersionRef | Unset = UNSET
    concurrency_policy: ConcurrencyPolicy | Unset = UNSET
    metadata: LaunchBatchTaskRequestMetadata | Unset = UNSET
    experiment: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        model_ref = self.model_ref.to_dict()

        tasks = []
        for tasks_item_data in self.tasks:
            tasks_item = tasks_item_data.to_dict()
            tasks.append(tasks_item)

        job_id = self.job_id

        harness_ref: dict[str, Any] | Unset = UNSET
        if not isinstance(self.harness_ref, Unset):
            harness_ref = self.harness_ref.to_dict()

        reward_ref: dict[str, Any] | Unset = UNSET
        if not isinstance(self.reward_ref, Unset):
            reward_ref = self.reward_ref.to_dict()

        concurrency_policy: dict[str, Any] | Unset = UNSET
        if not isinstance(self.concurrency_policy, Unset):
            concurrency_policy = self.concurrency_policy.to_dict()

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

        experiment = self.experiment

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "model_ref": model_ref,
                "tasks": tasks,
            }
        )
        if job_id is not UNSET:
            field_dict["job_id"] = job_id
        if harness_ref is not UNSET:
            field_dict["harness_ref"] = harness_ref
        if reward_ref is not UNSET:
            field_dict["reward_ref"] = reward_ref
        if concurrency_policy is not UNSET:
            field_dict["concurrency_policy"] = concurrency_policy
        if metadata is not UNSET:
            field_dict["metadata"] = metadata
        if experiment is not UNSET:
            field_dict["experiment"] = experiment

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.concurrency_policy import ConcurrencyPolicy
        from ..models.launch_batch_task_item import LaunchBatchTaskItem
        from ..models.launch_batch_task_request_metadata import (
            LaunchBatchTaskRequestMetadata,
        )
        from ..models.version_ref import VersionRef

        d = dict(src_dict)
        model_ref = VersionRef.from_dict(d.pop("model_ref"))

        tasks = []
        _tasks = d.pop("tasks")
        for tasks_item_data in _tasks:
            tasks_item = LaunchBatchTaskItem.from_dict(tasks_item_data)

            tasks.append(tasks_item)

        job_id = d.pop("job_id", UNSET)

        _harness_ref = d.pop("harness_ref", UNSET)
        harness_ref: VersionRef | Unset
        if isinstance(_harness_ref, Unset):
            harness_ref = UNSET
        else:
            harness_ref = VersionRef.from_dict(_harness_ref)

        _reward_ref = d.pop("reward_ref", UNSET)
        reward_ref: VersionRef | Unset
        if isinstance(_reward_ref, Unset):
            reward_ref = UNSET
        else:
            reward_ref = VersionRef.from_dict(_reward_ref)

        _concurrency_policy = d.pop("concurrency_policy", UNSET)
        concurrency_policy: ConcurrencyPolicy | Unset
        if isinstance(_concurrency_policy, Unset):
            concurrency_policy = UNSET
        else:
            concurrency_policy = ConcurrencyPolicy.from_dict(_concurrency_policy)

        _metadata = d.pop("metadata", UNSET)
        metadata: LaunchBatchTaskRequestMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = LaunchBatchTaskRequestMetadata.from_dict(_metadata)

        experiment = d.pop("experiment", UNSET)

        launch_batch_task_request = cls(
            model_ref=model_ref,
            tasks=tasks,
            job_id=job_id,
            harness_ref=harness_ref,
            reward_ref=reward_ref,
            concurrency_policy=concurrency_policy,
            metadata=metadata,
            experiment=experiment,
        )

        launch_batch_task_request.additional_properties = d
        return launch_batch_task_request

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
