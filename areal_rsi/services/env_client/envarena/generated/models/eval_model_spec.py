from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.eval_model_spec_parameters import EvalModelSpecParameters


T = TypeVar("T", bound="EvalModelSpec")


@_attrs_define
class EvalModelSpec:
    """
    Attributes:
        model_name (str | Unset):
        provider (str | Unset):
        gateway_ref (str | Unset): Server-side eval gateway reference resolved from ARENA_EVAL_GATEWAY_<REF>_URL, with
            ARENA_MODEL_GATEWAY_<REF>_URL and ARENA_GATEWAY_<REF>_URL as fallbacks.
        credential_ref (str | Unset): Server-side eval credential reference resolved from
            ARENA_EVAL_CREDENTIAL_<REF>_API_KEY, with ARENA_MODEL_CREDENTIAL_<REF>_API_KEY and
            ARENA_CREDENTIAL_<REF>_API_KEY as fallbacks.
        parameters (EvalModelSpecParameters | Unset):
    """

    model_name: str | Unset = UNSET
    provider: str | Unset = UNSET
    gateway_ref: str | Unset = UNSET
    credential_ref: str | Unset = UNSET
    parameters: EvalModelSpecParameters | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        model_name = self.model_name

        provider = self.provider

        gateway_ref = self.gateway_ref

        credential_ref = self.credential_ref

        parameters: dict[str, Any] | Unset = UNSET
        if not isinstance(self.parameters, Unset):
            parameters = self.parameters.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if model_name is not UNSET:
            field_dict["model_name"] = model_name
        if provider is not UNSET:
            field_dict["provider"] = provider
        if gateway_ref is not UNSET:
            field_dict["gateway_ref"] = gateway_ref
        if credential_ref is not UNSET:
            field_dict["credential_ref"] = credential_ref
        if parameters is not UNSET:
            field_dict["parameters"] = parameters

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.eval_model_spec_parameters import EvalModelSpecParameters

        d = dict(src_dict)
        model_name = d.pop("model_name", UNSET)

        provider = d.pop("provider", UNSET)

        gateway_ref = d.pop("gateway_ref", UNSET)

        credential_ref = d.pop("credential_ref", UNSET)

        _parameters = d.pop("parameters", UNSET)
        parameters: EvalModelSpecParameters | Unset
        if isinstance(_parameters, Unset):
            parameters = UNSET
        else:
            parameters = EvalModelSpecParameters.from_dict(_parameters)

        eval_model_spec = cls(
            model_name=model_name,
            provider=provider,
            gateway_ref=gateway_ref,
            credential_ref=credential_ref,
            parameters=parameters,
        )

        eval_model_spec.additional_properties = d
        return eval_model_spec

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
