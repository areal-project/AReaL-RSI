from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_spec import ModelSpec


T = TypeVar("T", bound="CreateModelVersionRequest")


@_attrs_define
class CreateModelVersionRequest:
    """
    Attributes:
        version (str):
        spec (ModelSpec): Compact model runtime config. EnvArena recognizes model_name, parameters, and env; unknown
            fields are preserved but are not used for routing.
        supersedes (str | Unset):
    """

    version: str
    spec: ModelSpec
    supersedes: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        version = self.version

        spec = self.spec.to_dict()

        supersedes = self.supersedes

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "version": version,
                "spec": spec,
            }
        )
        if supersedes is not UNSET:
            field_dict["supersedes"] = supersedes

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.model_spec import ModelSpec

        d = dict(src_dict)
        version = d.pop("version")

        spec = ModelSpec.from_dict(d.pop("spec"))

        supersedes = d.pop("supersedes", UNSET)

        create_model_version_request = cls(
            version=version,
            spec=spec,
            supersedes=supersedes,
        )

        create_model_version_request.additional_properties = d
        return create_model_version_request

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
