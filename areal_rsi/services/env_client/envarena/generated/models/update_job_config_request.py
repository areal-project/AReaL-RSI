from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="UpdateJobConfigRequest")


@_attrs_define
class UpdateJobConfigRequest:
    """
    Attributes:
        config_revision (int): The config_revision read from the job. Mismatch returns 409.
        max_parallel (int | Unset): New live concurrency limit. Omit to keep the current value.
        max_retries (int | Unset): New automatic retry budget per task. Omit to keep the current value.
        retry_backoff_seconds (int | Unset): New backoff between automatic retries. Omit to keep the current value.
    """

    config_revision: int
    max_parallel: int | Unset = UNSET
    max_retries: int | Unset = UNSET
    retry_backoff_seconds: int | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        config_revision = self.config_revision

        max_parallel = self.max_parallel

        max_retries = self.max_retries

        retry_backoff_seconds = self.retry_backoff_seconds

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "config_revision": config_revision,
            }
        )
        if max_parallel is not UNSET:
            field_dict["max_parallel"] = max_parallel
        if max_retries is not UNSET:
            field_dict["max_retries"] = max_retries
        if retry_backoff_seconds is not UNSET:
            field_dict["retry_backoff_seconds"] = retry_backoff_seconds

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        config_revision = d.pop("config_revision")

        max_parallel = d.pop("max_parallel", UNSET)

        max_retries = d.pop("max_retries", UNSET)

        retry_backoff_seconds = d.pop("retry_backoff_seconds", UNSET)

        update_job_config_request = cls(
            config_revision=config_revision,
            max_parallel=max_parallel,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
        )

        update_job_config_request.additional_properties = d
        return update_job_config_request

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
