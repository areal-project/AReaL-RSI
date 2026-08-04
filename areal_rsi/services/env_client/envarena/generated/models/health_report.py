from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.health_report_status import HealthReportStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.health_report_checks import HealthReportChecks


T = TypeVar("T", bound="HealthReport")


@_attrs_define
class HealthReport:
    """
    Attributes:
        status (HealthReportStatus):
        checks (HealthReportChecks):
        instance_id (str | Unset):
    """

    status: HealthReportStatus
    checks: HealthReportChecks
    instance_id: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        status = self.status.value

        checks = self.checks.to_dict()

        instance_id = self.instance_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "status": status,
                "checks": checks,
            }
        )
        if instance_id is not UNSET:
            field_dict["instance_id"] = instance_id

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.health_report_checks import HealthReportChecks

        d = dict(src_dict)
        status = HealthReportStatus(d.pop("status"))

        checks = HealthReportChecks.from_dict(d.pop("checks"))

        instance_id = d.pop("instance_id", UNSET)

        health_report = cls(
            status=status,
            checks=checks,
            instance_id=instance_id,
        )

        health_report.additional_properties = d
        return health_report

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
