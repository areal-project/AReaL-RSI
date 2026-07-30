from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.llm_model_endpoint_request_inbound_protos_item import (
    LLMModelEndpointRequestInboundProtosItem,
)
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.llm_endpoint_health_check import LLMEndpointHealthCheck
    from ..models.llm_model_endpoint_request_upstream_extra_params import (
        LLMModelEndpointRequestUpstreamExtraParams,
    )


T = TypeVar("T", bound="LLMModelEndpointRequest")


@_attrs_define
class LLMModelEndpointRequest:
    """
    Attributes:
        upstream_model (str):
        base_url (str):
        inbound_protos (list[LLMModelEndpointRequestInboundProtosItem]):
        endpoint_id (str | Unset): Required to preserve an existing endpoint on PATCH; omitted IDs are generated as new
            endpoints.
        api_key (str | Unset): Omit on PATCH to retain the existing endpoint key; pass an empty string to clear it.
        weight (float | Unset):  Default: 1.0.
        enabled (bool | Unset):  Default: True.
        upstream_extra_params (LLMModelEndpointRequestUpstreamExtraParams | Unset): Endpoint-specific JSON fields to
            merge into the upstream request. Omit on PATCH to preserve the existing value; pass {} to clear it.
        health_check (LLMEndpointHealthCheck | Unset):
    """

    upstream_model: str
    base_url: str
    inbound_protos: list[LLMModelEndpointRequestInboundProtosItem]
    endpoint_id: str | Unset = UNSET
    api_key: str | Unset = UNSET
    weight: float | Unset = 1.0
    enabled: bool | Unset = True
    upstream_extra_params: LLMModelEndpointRequestUpstreamExtraParams | Unset = UNSET
    health_check: LLMEndpointHealthCheck | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        upstream_model = self.upstream_model

        base_url = self.base_url

        inbound_protos = []
        for inbound_protos_item_data in self.inbound_protos:
            inbound_protos_item = inbound_protos_item_data.value
            inbound_protos.append(inbound_protos_item)

        endpoint_id = self.endpoint_id

        api_key = self.api_key

        weight = self.weight

        enabled = self.enabled

        upstream_extra_params: dict[str, Any] | Unset = UNSET
        if not isinstance(self.upstream_extra_params, Unset):
            upstream_extra_params = self.upstream_extra_params.to_dict()

        health_check: dict[str, Any] | Unset = UNSET
        if not isinstance(self.health_check, Unset):
            health_check = self.health_check.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "upstream_model": upstream_model,
                "base_url": base_url,
                "inbound_protos": inbound_protos,
            }
        )
        if endpoint_id is not UNSET:
            field_dict["endpoint_id"] = endpoint_id
        if api_key is not UNSET:
            field_dict["api_key"] = api_key
        if weight is not UNSET:
            field_dict["weight"] = weight
        if enabled is not UNSET:
            field_dict["enabled"] = enabled
        if upstream_extra_params is not UNSET:
            field_dict["upstream_extra_params"] = upstream_extra_params
        if health_check is not UNSET:
            field_dict["health_check"] = health_check

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.llm_endpoint_health_check import LLMEndpointHealthCheck
        from ..models.llm_model_endpoint_request_upstream_extra_params import (
            LLMModelEndpointRequestUpstreamExtraParams,
        )

        d = dict(src_dict)
        upstream_model = d.pop("upstream_model")

        base_url = d.pop("base_url")

        inbound_protos = []
        _inbound_protos = d.pop("inbound_protos")
        for inbound_protos_item_data in _inbound_protos:
            inbound_protos_item = LLMModelEndpointRequestInboundProtosItem(
                inbound_protos_item_data
            )

            inbound_protos.append(inbound_protos_item)

        endpoint_id = d.pop("endpoint_id", UNSET)

        api_key = d.pop("api_key", UNSET)

        weight = d.pop("weight", UNSET)

        enabled = d.pop("enabled", UNSET)

        _upstream_extra_params = d.pop("upstream_extra_params", UNSET)
        upstream_extra_params: LLMModelEndpointRequestUpstreamExtraParams | Unset
        if isinstance(_upstream_extra_params, Unset):
            upstream_extra_params = UNSET
        else:
            upstream_extra_params = (
                LLMModelEndpointRequestUpstreamExtraParams.from_dict(
                    _upstream_extra_params
                )
            )

        _health_check = d.pop("health_check", UNSET)
        health_check: LLMEndpointHealthCheck | Unset
        if isinstance(_health_check, Unset):
            health_check = UNSET
        else:
            health_check = LLMEndpointHealthCheck.from_dict(_health_check)

        llm_model_endpoint_request = cls(
            upstream_model=upstream_model,
            base_url=base_url,
            inbound_protos=inbound_protos,
            endpoint_id=endpoint_id,
            api_key=api_key,
            weight=weight,
            enabled=enabled,
            upstream_extra_params=upstream_extra_params,
            health_check=health_check,
        )

        llm_model_endpoint_request.additional_properties = d
        return llm_model_endpoint_request

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
