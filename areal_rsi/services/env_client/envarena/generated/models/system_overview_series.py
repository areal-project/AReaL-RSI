from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.system_overview_series_point import SystemOverviewSeriesPoint


T = TypeVar("T", bound="SystemOverviewSeries")


@_attrs_define
class SystemOverviewSeries:
    """
    Attributes:
        metric (str):
        points (list[SystemOverviewSeriesPoint]):
    """

    metric: str
    points: list[SystemOverviewSeriesPoint]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        metric = self.metric

        points = []
        for points_item_data in self.points:
            points_item = points_item_data.to_dict()
            points.append(points_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "metric": metric,
                "points": points,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.system_overview_series_point import SystemOverviewSeriesPoint

        d = dict(src_dict)
        metric = d.pop("metric")

        points = []
        _points = d.pop("points")
        for points_item_data in _points:
            points_item = SystemOverviewSeriesPoint.from_dict(points_item_data)

            points.append(points_item)

        system_overview_series = cls(
            metric=metric,
            points=points,
        )

        system_overview_series.additional_properties = d
        return system_overview_series

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
