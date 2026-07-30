from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.llm_model_endpoint_inbound_protos_item import (
    LLMModelEndpointInboundProtosItem,
)
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.llm_endpoint_health import LLMEndpointHealth
    from ..models.llm_endpoint_health_check import LLMEndpointHealthCheck
    from ..models.llm_model_endpoint_upstream_extra_params import (
        LLMModelEndpointUpstreamExtraParams,
    )


T = TypeVar("T", bound="LLMModelEndpoint")


@_attrs_define
class LLMModelEndpoint:
    """
    Attributes:
        endpoint_id (str): Stable endpoint identifier within the model alias.
        upstream_model (str): Model identifier sent to the configured upstream.
        base_url (str): Per-endpoint upstream base URL.
        inbound_protos (list[LLMModelEndpointInboundProtosItem]): Protocols supported by this endpoint's upstream.
        weight (float):  Default: 1.0.
        enabled (bool):  Default: True.
        upstream_extra_params (LLMModelEndpointUpstreamExtraParams): Extra JSON fields merged into the request sent to
            this endpoint's upstream. Nested objects merge recursively and this configuration takes precedence over client
            fields. model, stream, messages, and input are reserved and must not be set.
        api_key (str | Unset): Optional endpoint access/upstream key. It is masked in every response. Example: ***.
        health_check (LLMEndpointHealthCheck | Unset):
        health (LLMEndpointHealth | Unset):
    """

    endpoint_id: str
    upstream_model: str
    base_url: str
    inbound_protos: list[LLMModelEndpointInboundProtosItem]
    upstream_extra_params: LLMModelEndpointUpstreamExtraParams
    weight: float = 1.0
    enabled: bool = True
    api_key: str | Unset = UNSET
    health_check: LLMEndpointHealthCheck | Unset = UNSET
    health: LLMEndpointHealth | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        endpoint_id = self.endpoint_id

        upstream_model = self.upstream_model

        base_url = self.base_url

        inbound_protos = []
        for inbound_protos_item_data in self.inbound_protos:
            inbound_protos_item = inbound_protos_item_data.value
            inbound_protos.append(inbound_protos_item)

        weight = self.weight

        enabled = self.enabled

        upstream_extra_params = self.upstream_extra_params.to_dict()

        api_key = self.api_key

        health_check: dict[str, Any] | Unset = UNSET
        if not isinstance(self.health_check, Unset):
            health_check = self.health_check.to_dict()

        health: dict[str, Any] | Unset = UNSET
        if not isinstance(self.health, Unset):
            health = self.health.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "endpoint_id": endpoint_id,
                "upstream_model": upstream_model,
                "base_url": base_url,
                "inbound_protos": inbound_protos,
                "weight": weight,
                "enabled": enabled,
                "upstream_extra_params": upstream_extra_params,
            }
        )
        if api_key is not UNSET:
            field_dict["api_key"] = api_key
        if health_check is not UNSET:
            field_dict["health_check"] = health_check
        if health is not UNSET:
            field_dict["health"] = health

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.llm_endpoint_health import LLMEndpointHealth
        from ..models.llm_endpoint_health_check import LLMEndpointHealthCheck
        from ..models.llm_model_endpoint_upstream_extra_params import (
            LLMModelEndpointUpstreamExtraParams,
        )

        d = dict(src_dict)
        endpoint_id = d.pop("endpoint_id")

        upstream_model = d.pop("upstream_model")

        base_url = d.pop("base_url")

        inbound_protos = []
        _inbound_protos = d.pop("inbound_protos")
        for inbound_protos_item_data in _inbound_protos:
            inbound_protos_item = LLMModelEndpointInboundProtosItem(
                inbound_protos_item_data
            )

            inbound_protos.append(inbound_protos_item)

        weight = d.pop("weight")

        enabled = d.pop("enabled")

        upstream_extra_params = LLMModelEndpointUpstreamExtraParams.from_dict(
            d.pop("upstream_extra_params")
        )

        api_key = d.pop("api_key", UNSET)

        _health_check = d.pop("health_check", UNSET)
        health_check: LLMEndpointHealthCheck | Unset
        if isinstance(_health_check, Unset):
            health_check = UNSET
        else:
            health_check = LLMEndpointHealthCheck.from_dict(_health_check)

        _health = d.pop("health", UNSET)
        health: LLMEndpointHealth | Unset
        if isinstance(_health, Unset):
            health = UNSET
        else:
            health = LLMEndpointHealth.from_dict(_health)

        llm_model_endpoint = cls(
            endpoint_id=endpoint_id,
            upstream_model=upstream_model,
            base_url=base_url,
            inbound_protos=inbound_protos,
            weight=weight,
            enabled=enabled,
            upstream_extra_params=upstream_extra_params,
            api_key=api_key,
            health_check=health_check,
            health=health,
        )

        llm_model_endpoint.additional_properties = d
        return llm_model_endpoint

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
