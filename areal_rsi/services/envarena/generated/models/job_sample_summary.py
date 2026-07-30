from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="JobSampleSummary")


@_attrs_define
class JobSampleSummary:
    """
    Attributes:
        sample_index (int):
        pass_count (int):
        total (int):
        pass_rate (float):
    """

    sample_index: int
    pass_count: int
    total: int
    pass_rate: float
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        sample_index = self.sample_index

        pass_count = self.pass_count

        total = self.total

        pass_rate = self.pass_rate

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "sample_index": sample_index,
                "pass_count": pass_count,
                "total": total,
                "pass_rate": pass_rate,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        sample_index = d.pop("sample_index")

        pass_count = d.pop("pass_count")

        total = d.pop("total")

        pass_rate = d.pop("pass_rate")

        job_sample_summary = cls(
            sample_index=sample_index,
            pass_count=pass_count,
            total=total,
            pass_rate=pass_rate,
        )

        job_sample_summary.additional_properties = d
        return job_sample_summary

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
