from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.reward_spec import RewardSpec


T = TypeVar("T", bound="UpdateRewardVersionRequest")


@_attrs_define
class UpdateRewardVersionRequest:
    """
    Attributes:
        spec (RewardSpec | Unset):
    """

    spec: RewardSpec | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        spec: dict[str, Any] | Unset = UNSET
        if not isinstance(self.spec, Unset):
            spec = self.spec.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if spec is not UNSET:
            field_dict["spec"] = spec

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.reward_spec import RewardSpec

        d = dict(src_dict)
        _spec = d.pop("spec", UNSET)
        spec: RewardSpec | Unset
        if isinstance(_spec, Unset):
            spec = UNSET
        else:
            spec = RewardSpec.from_dict(_spec)

        update_reward_version_request = cls(
            spec=spec,
        )

        update_reward_version_request.additional_properties = d
        return update_reward_version_request

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
