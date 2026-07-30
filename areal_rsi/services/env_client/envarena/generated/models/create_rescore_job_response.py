from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.create_rescore_job_response_agent_task_count import (
    CreateRescoreJobResponseAgentTaskCount,
)
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.benchmark_job import BenchmarkJob
    from ..models.version_ref import VersionRef


T = TypeVar("T", bound="CreateRescoreJobResponse")


@_attrs_define
class CreateRescoreJobResponse:
    """
    Attributes:
        job_id (str):
        task_ids (list[str]):
        source_job_id (str):
        reward_ref (VersionRef):
        reward_task_count (int): Number of derived Tasks that will run a Reward sandbox.
        agent_task_count (CreateRescoreJobResponseAgentTaskCount): Always zero; a RESCORE Job never launches
            Agent/Harness sandboxes.
        job (BenchmarkJob | Unset):
    """

    job_id: str
    task_ids: list[str]
    source_job_id: str
    reward_ref: VersionRef
    reward_task_count: int
    agent_task_count: CreateRescoreJobResponseAgentTaskCount
    job: BenchmarkJob | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        job_id = self.job_id

        task_ids = self.task_ids

        source_job_id = self.source_job_id

        reward_ref = self.reward_ref.to_dict()

        reward_task_count = self.reward_task_count

        agent_task_count = self.agent_task_count.value

        job: dict[str, Any] | Unset = UNSET
        if not isinstance(self.job, Unset):
            job = self.job.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "job_id": job_id,
                "task_ids": task_ids,
                "source_job_id": source_job_id,
                "reward_ref": reward_ref,
                "reward_task_count": reward_task_count,
                "agent_task_count": agent_task_count,
            }
        )
        if job is not UNSET:
            field_dict["job"] = job

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.benchmark_job import BenchmarkJob
        from ..models.version_ref import VersionRef

        d = dict(src_dict)
        job_id = d.pop("job_id")

        task_ids = cast(list[str], d.pop("task_ids"))

        source_job_id = d.pop("source_job_id")

        reward_ref = VersionRef.from_dict(d.pop("reward_ref"))

        reward_task_count = d.pop("reward_task_count")

        agent_task_count = CreateRescoreJobResponseAgentTaskCount(
            d.pop("agent_task_count")
        )

        _job = d.pop("job", UNSET)
        job: BenchmarkJob | Unset
        if isinstance(_job, Unset):
            job = UNSET
        else:
            job = BenchmarkJob.from_dict(_job)

        create_rescore_job_response = cls(
            job_id=job_id,
            task_ids=task_ids,
            source_job_id=source_job_id,
            reward_ref=reward_ref,
            reward_task_count=reward_task_count,
            agent_task_count=agent_task_count,
            job=job,
        )

        create_rescore_job_response.additional_properties = d
        return create_rescore_job_response

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
