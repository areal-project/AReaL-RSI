from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.retry_job_request_mode import RetryJobRequestMode
from ..types import UNSET, Unset

T = TypeVar("T", bound="RetryJobRequest")


@_attrs_define
class RetryJobRequest:
    """
    Attributes:
        mode (RetryJobRequestMode): failed = failed tasks only (CANCELLED excluded), all = every task, selected = the
            given task_ids.
        task_ids (list[str] | Unset): Required when mode=selected; every id must belong to the job.
        idempotency_key (str | Unset): Optional client key. Replaying the same key returns the recorded outcome instead
            of opening another generation.
    """

    mode: RetryJobRequestMode
    task_ids: list[str] | Unset = UNSET
    idempotency_key: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        mode = self.mode.value

        task_ids: list[str] | Unset = UNSET
        if not isinstance(self.task_ids, Unset):
            task_ids = self.task_ids

        idempotency_key = self.idempotency_key

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "mode": mode,
            }
        )
        if task_ids is not UNSET:
            field_dict["task_ids"] = task_ids
        if idempotency_key is not UNSET:
            field_dict["idempotency_key"] = idempotency_key

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        mode = RetryJobRequestMode(d.pop("mode"))

        task_ids = cast(list[str], d.pop("task_ids", UNSET))

        idempotency_key = d.pop("idempotency_key", UNSET)

        retry_job_request = cls(
            mode=mode,
            task_ids=task_ids,
            idempotency_key=idempotency_key,
        )

        retry_job_request.additional_properties = d
        return retry_job_request

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
