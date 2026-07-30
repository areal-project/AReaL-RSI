from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="JobTaskCounts")


@_attrs_define
class JobTaskCounts:
    """
    Attributes:
        total (int):
        done (int):
        failed (int):
        running (int):
        pending (int):
        cancelled (int | Unset): Subset of failed whose status is CANCELLED (tasks parked by
            stop/cancel, not genuine failures). Manual retry mode=failed
            skips them; failed - cancelled is the retry-failed candidate
            count.
    """

    total: int
    done: int
    failed: int
    running: int
    pending: int
    cancelled: int | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        total = self.total

        done = self.done

        failed = self.failed

        running = self.running

        pending = self.pending

        cancelled = self.cancelled

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "total": total,
                "done": done,
                "failed": failed,
                "running": running,
                "pending": pending,
            }
        )
        if cancelled is not UNSET:
            field_dict["cancelled"] = cancelled

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        total = d.pop("total")

        done = d.pop("done")

        failed = d.pop("failed")

        running = d.pop("running")

        pending = d.pop("pending")

        cancelled = d.pop("cancelled", UNSET)

        job_task_counts = cls(
            total=total,
            done=done,
            failed=failed,
            running=running,
            pending=pending,
            cancelled=cancelled,
        )

        job_task_counts.additional_properties = d
        return job_task_counts

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
