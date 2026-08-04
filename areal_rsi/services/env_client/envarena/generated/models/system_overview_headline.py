from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="SystemOverviewHeadline")


@_attrs_define
class SystemOverviewHeadline:
    """
    Attributes:
        max_task_state_age_seconds (float):
        arca_quota_in_use (int):
        arca_quota_limit (int):
        active_tasks (int):
        active_jobs (int):
        http_success_ratio (float | Unset):
        platform_success_ratio (float | Unset):
        max_task_state_status (str | Unset):
    """

    max_task_state_age_seconds: float
    arca_quota_in_use: int
    arca_quota_limit: int
    active_tasks: int
    active_jobs: int
    http_success_ratio: float | Unset = UNSET
    platform_success_ratio: float | Unset = UNSET
    max_task_state_status: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        max_task_state_age_seconds = self.max_task_state_age_seconds

        arca_quota_in_use = self.arca_quota_in_use

        arca_quota_limit = self.arca_quota_limit

        active_tasks = self.active_tasks

        active_jobs = self.active_jobs

        http_success_ratio = self.http_success_ratio

        platform_success_ratio = self.platform_success_ratio

        max_task_state_status = self.max_task_state_status

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "max_task_state_age_seconds": max_task_state_age_seconds,
                "arca_quota_in_use": arca_quota_in_use,
                "arca_quota_limit": arca_quota_limit,
                "active_tasks": active_tasks,
                "active_jobs": active_jobs,
            }
        )
        if http_success_ratio is not UNSET:
            field_dict["http_success_ratio"] = http_success_ratio
        if platform_success_ratio is not UNSET:
            field_dict["platform_success_ratio"] = platform_success_ratio
        if max_task_state_status is not UNSET:
            field_dict["max_task_state_status"] = max_task_state_status

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        max_task_state_age_seconds = d.pop("max_task_state_age_seconds")

        arca_quota_in_use = d.pop("arca_quota_in_use")

        arca_quota_limit = d.pop("arca_quota_limit")

        active_tasks = d.pop("active_tasks")

        active_jobs = d.pop("active_jobs")

        http_success_ratio = d.pop("http_success_ratio", UNSET)

        platform_success_ratio = d.pop("platform_success_ratio", UNSET)

        max_task_state_status = d.pop("max_task_state_status", UNSET)

        system_overview_headline = cls(
            max_task_state_age_seconds=max_task_state_age_seconds,
            arca_quota_in_use=arca_quota_in_use,
            arca_quota_limit=arca_quota_limit,
            active_tasks=active_tasks,
            active_jobs=active_jobs,
            http_success_ratio=http_success_ratio,
            platform_success_ratio=platform_success_ratio,
            max_task_state_status=max_task_state_status,
        )

        system_overview_headline.additional_properties = d
        return system_overview_headline

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
