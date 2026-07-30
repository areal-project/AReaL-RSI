from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.version_ref import VersionRef


T = TypeVar("T", bound="PostOpenapiV1TasksTaskIdReevalBody")


@_attrs_define
class PostOpenapiV1TasksTaskIdReevalBody:
    """
    Attributes:
        reward_ref (VersionRef | Unset):
        reason (str | Unset):
    """

    reward_ref: VersionRef | Unset = UNSET
    reason: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        reward_ref: dict[str, Any] | Unset = UNSET
        if not isinstance(self.reward_ref, Unset):
            reward_ref = self.reward_ref.to_dict()

        reason = self.reason

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if reward_ref is not UNSET:
            field_dict["reward_ref"] = reward_ref
        if reason is not UNSET:
            field_dict["reason"] = reason

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.version_ref import VersionRef

        d = dict(src_dict)
        _reward_ref = d.pop("reward_ref", UNSET)
        reward_ref: VersionRef | Unset
        if isinstance(_reward_ref, Unset):
            reward_ref = UNSET
        else:
            reward_ref = VersionRef.from_dict(_reward_ref)

        reason = d.pop("reason", UNSET)

        post_openapi_v1_tasks_task_id_reeval_body = cls(
            reward_ref=reward_ref,
            reason=reason,
        )

        post_openapi_v1_tasks_task_id_reeval_body.additional_properties = d
        return post_openapi_v1_tasks_task_id_reeval_body

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
