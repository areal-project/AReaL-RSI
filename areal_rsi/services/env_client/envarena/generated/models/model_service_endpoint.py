from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ModelServiceEndpoint")


@_attrs_define
class ModelServiceEndpoint:
    """
    Attributes:
        base_url (str | Unset):
        v1_url (str | Unset):
        models_url (str | Unset):
        health_url (str | Unset):
    """

    base_url: str | Unset = UNSET
    v1_url: str | Unset = UNSET
    models_url: str | Unset = UNSET
    health_url: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        base_url = self.base_url

        v1_url = self.v1_url

        models_url = self.models_url

        health_url = self.health_url

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if base_url is not UNSET:
            field_dict["base_url"] = base_url
        if v1_url is not UNSET:
            field_dict["v1_url"] = v1_url
        if models_url is not UNSET:
            field_dict["models_url"] = models_url
        if health_url is not UNSET:
            field_dict["health_url"] = health_url

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        base_url = d.pop("base_url", UNSET)

        v1_url = d.pop("v1_url", UNSET)

        models_url = d.pop("models_url", UNSET)

        health_url = d.pop("health_url", UNSET)

        model_service_endpoint = cls(
            base_url=base_url,
            v1_url=v1_url,
            models_url=models_url,
            health_url=health_url,
        )

        model_service_endpoint.additional_properties = d
        return model_service_endpoint

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
