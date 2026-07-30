from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="LLMEndpointHealthCheck")


@_attrs_define
class LLMEndpointHealthCheck:
    """
    Attributes:
        url (str): HTTP(S) URL to probe with GET.
        expected_status (int): Exact HTTP status required for an endpoint to remain routable. Default: 200.
        interval_seconds (int): Probe interval. A failed probe removes this endpoint from weighted selection. Default:
            30.
    """

    url: str
    expected_status: int = 200
    interval_seconds: int = 30
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        url = self.url

        expected_status = self.expected_status

        interval_seconds = self.interval_seconds

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "url": url,
                "expected_status": expected_status,
                "interval_seconds": interval_seconds,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        url = d.pop("url")

        expected_status = d.pop("expected_status")

        interval_seconds = d.pop("interval_seconds")

        llm_endpoint_health_check = cls(
            url=url,
            expected_status=expected_status,
            interval_seconds=interval_seconds,
        )

        llm_endpoint_health_check.additional_properties = d
        return llm_endpoint_health_check

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
