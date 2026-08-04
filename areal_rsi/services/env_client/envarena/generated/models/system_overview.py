from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.health_report import HealthReport
    from ..models.system_overview_headline import SystemOverviewHeadline
    from ..models.system_overview_series import SystemOverviewSeries
    from ..models.system_overview_state_row import SystemOverviewStateRow
    from ..models.system_overview_terminal_tasks_1h_type_0 import (
        SystemOverviewTerminalTasks1HType0,
    )


T = TypeVar("T", bound="SystemOverview")


@_attrs_define
class SystemOverview:
    """
    Attributes:
        generated_at (datetime.datetime):
        window_seconds (int):
        bucket_seconds (int):
        health (HealthReport):
        headline (SystemOverviewHeadline):
        tasks_in_state (list[SystemOverviewStateRow] | None):
        jobs_in_state (list[SystemOverviewStateRow] | None):
        terminal_tasks_1h (None | SystemOverviewTerminalTasks1HType0):
        trends (list[SystemOverviewSeries] | None):
        warnings (list[str] | Unset):
    """

    generated_at: datetime.datetime
    window_seconds: int
    bucket_seconds: int
    health: HealthReport
    headline: SystemOverviewHeadline
    tasks_in_state: list[SystemOverviewStateRow] | None
    jobs_in_state: list[SystemOverviewStateRow] | None
    terminal_tasks_1h: None | SystemOverviewTerminalTasks1HType0
    trends: list[SystemOverviewSeries] | None
    warnings: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.system_overview_terminal_tasks_1h_type_0 import (
            SystemOverviewTerminalTasks1HType0,
        )

        generated_at = self.generated_at.isoformat()

        window_seconds = self.window_seconds

        bucket_seconds = self.bucket_seconds

        health = self.health.to_dict()

        headline = self.headline.to_dict()

        tasks_in_state: list[dict[str, Any]] | None
        if isinstance(self.tasks_in_state, list):
            tasks_in_state = []
            for tasks_in_state_type_0_item_data in self.tasks_in_state:
                tasks_in_state_type_0_item = tasks_in_state_type_0_item_data.to_dict()
                tasks_in_state.append(tasks_in_state_type_0_item)

        else:
            tasks_in_state = self.tasks_in_state

        jobs_in_state: list[dict[str, Any]] | None
        if isinstance(self.jobs_in_state, list):
            jobs_in_state = []
            for jobs_in_state_type_0_item_data in self.jobs_in_state:
                jobs_in_state_type_0_item = jobs_in_state_type_0_item_data.to_dict()
                jobs_in_state.append(jobs_in_state_type_0_item)

        else:
            jobs_in_state = self.jobs_in_state

        terminal_tasks_1h: dict[str, Any] | None
        if isinstance(self.terminal_tasks_1h, SystemOverviewTerminalTasks1HType0):
            terminal_tasks_1h = self.terminal_tasks_1h.to_dict()
        else:
            terminal_tasks_1h = self.terminal_tasks_1h

        trends: list[dict[str, Any]] | None
        if isinstance(self.trends, list):
            trends = []
            for trends_type_0_item_data in self.trends:
                trends_type_0_item = trends_type_0_item_data.to_dict()
                trends.append(trends_type_0_item)

        else:
            trends = self.trends

        warnings: list[str] | Unset = UNSET
        if not isinstance(self.warnings, Unset):
            warnings = self.warnings

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "generated_at": generated_at,
                "window_seconds": window_seconds,
                "bucket_seconds": bucket_seconds,
                "health": health,
                "headline": headline,
                "tasks_in_state": tasks_in_state,
                "jobs_in_state": jobs_in_state,
                "terminal_tasks_1h": terminal_tasks_1h,
                "trends": trends,
            }
        )
        if warnings is not UNSET:
            field_dict["warnings"] = warnings

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.health_report import HealthReport
        from ..models.system_overview_headline import SystemOverviewHeadline
        from ..models.system_overview_series import SystemOverviewSeries
        from ..models.system_overview_state_row import SystemOverviewStateRow
        from ..models.system_overview_terminal_tasks_1h_type_0 import (
            SystemOverviewTerminalTasks1HType0,
        )

        d = dict(src_dict)
        generated_at = isoparse(d.pop("generated_at"))

        window_seconds = d.pop("window_seconds")

        bucket_seconds = d.pop("bucket_seconds")

        health = HealthReport.from_dict(d.pop("health"))

        headline = SystemOverviewHeadline.from_dict(d.pop("headline"))

        def _parse_tasks_in_state(data: object) -> list[SystemOverviewStateRow] | None:
            if data is None:
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                tasks_in_state_type_0 = []
                _tasks_in_state_type_0 = data
                for tasks_in_state_type_0_item_data in _tasks_in_state_type_0:
                    tasks_in_state_type_0_item = SystemOverviewStateRow.from_dict(
                        tasks_in_state_type_0_item_data
                    )

                    tasks_in_state_type_0.append(tasks_in_state_type_0_item)

                return tasks_in_state_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[SystemOverviewStateRow] | None, data)

        tasks_in_state = _parse_tasks_in_state(d.pop("tasks_in_state"))

        def _parse_jobs_in_state(data: object) -> list[SystemOverviewStateRow] | None:
            if data is None:
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                jobs_in_state_type_0 = []
                _jobs_in_state_type_0 = data
                for jobs_in_state_type_0_item_data in _jobs_in_state_type_0:
                    jobs_in_state_type_0_item = SystemOverviewStateRow.from_dict(
                        jobs_in_state_type_0_item_data
                    )

                    jobs_in_state_type_0.append(jobs_in_state_type_0_item)

                return jobs_in_state_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[SystemOverviewStateRow] | None, data)

        jobs_in_state = _parse_jobs_in_state(d.pop("jobs_in_state"))

        def _parse_terminal_tasks_1h(
            data: object,
        ) -> None | SystemOverviewTerminalTasks1HType0:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                terminal_tasks_1h_type_0 = SystemOverviewTerminalTasks1HType0.from_dict(
                    data
                )

                return terminal_tasks_1h_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | SystemOverviewTerminalTasks1HType0, data)

        terminal_tasks_1h = _parse_terminal_tasks_1h(d.pop("terminal_tasks_1h"))

        def _parse_trends(data: object) -> list[SystemOverviewSeries] | None:
            if data is None:
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                trends_type_0 = []
                _trends_type_0 = data
                for trends_type_0_item_data in _trends_type_0:
                    trends_type_0_item = SystemOverviewSeries.from_dict(
                        trends_type_0_item_data
                    )

                    trends_type_0.append(trends_type_0_item)

                return trends_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[SystemOverviewSeries] | None, data)

        trends = _parse_trends(d.pop("trends"))

        warnings = cast(list[str], d.pop("warnings", UNSET))

        system_overview = cls(
            generated_at=generated_at,
            window_seconds=window_seconds,
            bucket_seconds=bucket_seconds,
            health=health,
            headline=headline,
            tasks_in_state=tasks_in_state,
            jobs_in_state=jobs_in_state,
            terminal_tasks_1h=terminal_tasks_1h,
            trends=trends,
            warnings=warnings,
        )

        system_overview.additional_properties = d
        return system_overview

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
