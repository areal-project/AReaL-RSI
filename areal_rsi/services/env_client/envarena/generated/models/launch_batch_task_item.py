from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.version_ref import VersionRef


T = TypeVar("T", bound="LaunchBatchTaskItem")


@_attrs_define
class LaunchBatchTaskItem:
    """
    Attributes:
        data_id (str):  Example: env:tau3-retail-001@0.1.0.
        harness_ref (VersionRef | Unset):
        reward_ref (VersionRef | Unset):
    """

    data_id: str
    harness_ref: VersionRef | Unset = UNSET
    reward_ref: VersionRef | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data_id = self.data_id

        harness_ref: dict[str, Any] | Unset = UNSET
        if not isinstance(self.harness_ref, Unset):
            harness_ref = self.harness_ref.to_dict()

        reward_ref: dict[str, Any] | Unset = UNSET
        if not isinstance(self.reward_ref, Unset):
            reward_ref = self.reward_ref.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "data_id": data_id,
            }
        )
        if harness_ref is not UNSET:
            field_dict["harness_ref"] = harness_ref
        if reward_ref is not UNSET:
            field_dict["reward_ref"] = reward_ref

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.version_ref import VersionRef

        d = dict(src_dict)
        data_id = d.pop("data_id")

        _harness_ref = d.pop("harness_ref", UNSET)
        harness_ref: VersionRef | Unset
        if isinstance(_harness_ref, Unset):
            harness_ref = UNSET
        else:
            harness_ref = VersionRef.from_dict(_harness_ref)

        _reward_ref = d.pop("reward_ref", UNSET)
        reward_ref: VersionRef | Unset
        if isinstance(_reward_ref, Unset):
            reward_ref = UNSET
        else:
            reward_ref = VersionRef.from_dict(_reward_ref)

        launch_batch_task_item = cls(
            data_id=data_id,
            harness_ref=harness_ref,
            reward_ref=reward_ref,
        )

        launch_batch_task_item.additional_properties = d
        return launch_batch_task_item

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
