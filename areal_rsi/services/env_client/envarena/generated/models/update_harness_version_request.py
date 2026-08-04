from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.harness_spec import HarnessSpec


T = TypeVar("T", bound="UpdateHarnessVersionRequest")


@_attrs_define
class UpdateHarnessVersionRequest:
    """
    Attributes:
        storage_ref (str | Unset): Registry material storage binding. `default` is the immutable OSS_* environment
            configuration.
        spec (HarnessSpec | Unset): Harness is system_prompt + tool_refs + skill_refs plus a versioned agent runtime
            binary. The platform still chooses the execution substrate from the bound Env runtime.
    """

    storage_ref: str | Unset = UNSET
    spec: HarnessSpec | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        storage_ref = self.storage_ref

        spec: dict[str, Any] | Unset = UNSET
        if not isinstance(self.spec, Unset):
            spec = self.spec.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if storage_ref is not UNSET:
            field_dict["storage_ref"] = storage_ref
        if spec is not UNSET:
            field_dict["spec"] = spec

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.harness_spec import HarnessSpec

        d = dict(src_dict)
        storage_ref = d.pop("storage_ref", UNSET)

        _spec = d.pop("spec", UNSET)
        spec: HarnessSpec | Unset
        if isinstance(_spec, Unset):
            spec = UNSET
        else:
            spec = HarnessSpec.from_dict(_spec)

        update_harness_version_request = cls(
            storage_ref=storage_ref,
            spec=spec,
        )

        update_harness_version_request.additional_properties = d
        return update_harness_version_request

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
