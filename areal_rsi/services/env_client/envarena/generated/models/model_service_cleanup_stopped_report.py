from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.model_service_cleanup_stopped_report_failures_item import (
        ModelServiceCleanupStoppedReportFailuresItem,
    )


T = TypeVar("T", bound="ModelServiceCleanupStoppedReport")


@_attrs_define
class ModelServiceCleanupStoppedReport:
    """
    Attributes:
        deleted (int):
        skipped_active (int):
        failures (list[ModelServiceCleanupStoppedReportFailuresItem]):
    """

    deleted: int
    skipped_active: int
    failures: list[ModelServiceCleanupStoppedReportFailuresItem]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        deleted = self.deleted

        skipped_active = self.skipped_active

        failures = []
        for failures_item_data in self.failures:
            failures_item = failures_item_data.to_dict()
            failures.append(failures_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "deleted": deleted,
                "skipped_active": skipped_active,
                "failures": failures,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.model_service_cleanup_stopped_report_failures_item import (
            ModelServiceCleanupStoppedReportFailuresItem,
        )

        d = dict(src_dict)
        deleted = d.pop("deleted")

        skipped_active = d.pop("skipped_active")

        failures = []
        _failures = d.pop("failures")
        for failures_item_data in _failures:
            failures_item = ModelServiceCleanupStoppedReportFailuresItem.from_dict(
                failures_item_data
            )

            failures.append(failures_item)

        model_service_cleanup_stopped_report = cls(
            deleted=deleted,
            skipped_active=skipped_active,
            failures=failures,
        )

        model_service_cleanup_stopped_report.additional_properties = d
        return model_service_cleanup_stopped_report

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
