from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.recovery_result_skipped_sandboxes import (
        RecoveryResultSkippedSandboxes,
    )


T = TypeVar("T", bound="RecoveryResult")


@_attrs_define
class RecoveryResult:
    """
    Attributes:
        recovered_jobs (list[str]):
        reset_tasks (int):
        destroyed_sandboxes (list[str] | Unset):
        skipped_sandboxes (RecoveryResultSkippedSandboxes | Unset):
    """

    recovered_jobs: list[str]
    reset_tasks: int
    destroyed_sandboxes: list[str] | Unset = UNSET
    skipped_sandboxes: RecoveryResultSkippedSandboxes | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        recovered_jobs = self.recovered_jobs

        reset_tasks = self.reset_tasks

        destroyed_sandboxes: list[str] | Unset = UNSET
        if not isinstance(self.destroyed_sandboxes, Unset):
            destroyed_sandboxes = self.destroyed_sandboxes

        skipped_sandboxes: dict[str, Any] | Unset = UNSET
        if not isinstance(self.skipped_sandboxes, Unset):
            skipped_sandboxes = self.skipped_sandboxes.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "recovered_jobs": recovered_jobs,
                "reset_tasks": reset_tasks,
            }
        )
        if destroyed_sandboxes is not UNSET:
            field_dict["destroyed_sandboxes"] = destroyed_sandboxes
        if skipped_sandboxes is not UNSET:
            field_dict["skipped_sandboxes"] = skipped_sandboxes

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.recovery_result_skipped_sandboxes import (
            RecoveryResultSkippedSandboxes,
        )

        d = dict(src_dict)
        recovered_jobs = cast(list[str], d.pop("recovered_jobs"))

        reset_tasks = d.pop("reset_tasks")

        destroyed_sandboxes = cast(list[str], d.pop("destroyed_sandboxes", UNSET))

        _skipped_sandboxes = d.pop("skipped_sandboxes", UNSET)
        skipped_sandboxes: RecoveryResultSkippedSandboxes | Unset
        if isinstance(_skipped_sandboxes, Unset):
            skipped_sandboxes = UNSET
        else:
            skipped_sandboxes = RecoveryResultSkippedSandboxes.from_dict(
                _skipped_sandboxes
            )

        recovery_result = cls(
            recovered_jobs=recovered_jobs,
            reset_tasks=reset_tasks,
            destroyed_sandboxes=destroyed_sandboxes,
            skipped_sandboxes=skipped_sandboxes,
        )

        recovery_result.additional_properties = d
        return recovery_result

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
