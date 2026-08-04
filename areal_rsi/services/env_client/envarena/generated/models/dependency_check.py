from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.dependency_check_status import DependencyCheckStatus
from ..types import UNSET, Unset

T = TypeVar("T", bound="DependencyCheck")


@_attrs_define
class DependencyCheck:
    """
    Attributes:
        status (DependencyCheckStatus):
        latency_ms (float):
        error (str | Unset):
        detail (str | Unset):
    """

    status: DependencyCheckStatus
    latency_ms: float
    error: str | Unset = UNSET
    detail: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        status = self.status.value

        latency_ms = self.latency_ms

        error = self.error

        detail = self.detail

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "status": status,
                "latency_ms": latency_ms,
            }
        )
        if error is not UNSET:
            field_dict["error"] = error
        if detail is not UNSET:
            field_dict["detail"] = detail

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        status = DependencyCheckStatus(d.pop("status"))

        latency_ms = d.pop("latency_ms")

        error = d.pop("error", UNSET)

        detail = d.pop("detail", UNSET)

        dependency_check = cls(
            status=status,
            latency_ms=latency_ms,
            error=error,
            detail=detail,
        )

        dependency_check.additional_properties = d
        return dependency_check

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
