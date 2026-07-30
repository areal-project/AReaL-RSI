from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.job_env_list_response_status_counts import (
        JobEnvListResponseStatusCounts,
    )
    from ..models.job_env_summary import JobEnvSummary
    from ..models.job_result import JobResult
    from ..models.job_task_counts import JobTaskCounts


T = TypeVar("T", bound="JobEnvListResponse")


@_attrs_define
class JobEnvListResponse:
    """
    Attributes:
        items (list[JobEnvSummary]):
        total (int): Number of Env groups matching the status filter.
        offset (int):
        limit (int):
        env_count (int): Number of Env groups before filtering.
        task_count (int): Number of Tasks before filtering.
        filtered_task_count (int): Number of Tasks matching the status filter across all matching Env groups.
        status_counts (JobEnvListResponseStatusCounts):
        task_counts (JobTaskCounts):
        result (JobResult | Unset):
    """

    items: list[JobEnvSummary]
    total: int
    offset: int
    limit: int
    env_count: int
    task_count: int
    filtered_task_count: int
    status_counts: JobEnvListResponseStatusCounts
    task_counts: JobTaskCounts
    result: JobResult | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        items = []
        for items_item_data in self.items:
            items_item = items_item_data.to_dict()
            items.append(items_item)

        total = self.total

        offset = self.offset

        limit = self.limit

        env_count = self.env_count

        task_count = self.task_count

        filtered_task_count = self.filtered_task_count

        status_counts = self.status_counts.to_dict()

        task_counts = self.task_counts.to_dict()

        result: dict[str, Any] | Unset = UNSET
        if not isinstance(self.result, Unset):
            result = self.result.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "items": items,
                "total": total,
                "offset": offset,
                "limit": limit,
                "env_count": env_count,
                "task_count": task_count,
                "filtered_task_count": filtered_task_count,
                "status_counts": status_counts,
                "task_counts": task_counts,
            }
        )
        if result is not UNSET:
            field_dict["result"] = result

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.job_env_list_response_status_counts import (
            JobEnvListResponseStatusCounts,
        )
        from ..models.job_env_summary import JobEnvSummary
        from ..models.job_result import JobResult
        from ..models.job_task_counts import JobTaskCounts

        d = dict(src_dict)
        items = []
        _items = d.pop("items")
        for items_item_data in _items:
            items_item = JobEnvSummary.from_dict(items_item_data)

            items.append(items_item)

        total = d.pop("total")

        offset = d.pop("offset")

        limit = d.pop("limit")

        env_count = d.pop("env_count")

        task_count = d.pop("task_count")

        filtered_task_count = d.pop("filtered_task_count")

        status_counts = JobEnvListResponseStatusCounts.from_dict(d.pop("status_counts"))

        task_counts = JobTaskCounts.from_dict(d.pop("task_counts"))

        _result = d.pop("result", UNSET)
        result: JobResult | Unset
        if isinstance(_result, Unset):
            result = UNSET
        else:
            result = JobResult.from_dict(_result)

        job_env_list_response = cls(
            items=items,
            total=total,
            offset=offset,
            limit=limit,
            env_count=env_count,
            task_count=task_count,
            filtered_task_count=filtered_task_count,
            status_counts=status_counts,
            task_counts=task_counts,
            result=result,
        )

        job_env_list_response.additional_properties = d
        return job_env_list_response

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
