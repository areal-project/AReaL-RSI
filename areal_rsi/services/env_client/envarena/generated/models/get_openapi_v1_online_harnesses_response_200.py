from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="GetOpenapiV1OnlineHarnessesResponse200")


@_attrs_define
class GetOpenapiV1OnlineHarnessesResponse200:
    """
    Attributes:
        harness (list[str]):  Example: ['tau3-runner@0.1.0'].
    """

    harness: list[str]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        harness = self.harness

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "harness": harness,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        harness = cast(list[str], d.pop("harness"))

        get_openapi_v1_online_harnesses_response_200 = cls(
            harness=harness,
        )

        get_openapi_v1_online_harnesses_response_200.additional_properties = d
        return get_openapi_v1_online_harnesses_response_200

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
