from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define

from ..models.model_service_gateway_config_inbound_protos_item import (
    ModelServiceGatewayConfigInboundProtosItem,
)
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_service_gateway_config_upstream_extra_params import (
        ModelServiceGatewayConfigUpstreamExtraParams,
    )


T = TypeVar("T", bound="ModelServiceGatewayConfig")


@_attrs_define
class ModelServiceGatewayConfig:
    """
    Attributes:
        inbound_protos (list[ModelServiceGatewayConfigInboundProtosItem] | Unset): Default gateway protocols used when
            model-service registers a new endpoint for this catalog model.
        upstream_extra_params (ModelServiceGatewayConfigUpstreamExtraParams | Unset): Default upstream extra params
            copied to model-service managed gateway endpoints.
    """

    inbound_protos: list[ModelServiceGatewayConfigInboundProtosItem] | Unset = UNSET
    upstream_extra_params: ModelServiceGatewayConfigUpstreamExtraParams | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        inbound_protos: list[str] | Unset = UNSET
        if not isinstance(self.inbound_protos, Unset):
            inbound_protos = []
            for inbound_protos_item_data in self.inbound_protos:
                inbound_protos_item = inbound_protos_item_data.value
                inbound_protos.append(inbound_protos_item)

        upstream_extra_params: dict[str, Any] | Unset = UNSET
        if not isinstance(self.upstream_extra_params, Unset):
            upstream_extra_params = self.upstream_extra_params.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if inbound_protos is not UNSET:
            field_dict["inbound_protos"] = inbound_protos
        if upstream_extra_params is not UNSET:
            field_dict["upstream_extra_params"] = upstream_extra_params

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.model_service_gateway_config_upstream_extra_params import (
            ModelServiceGatewayConfigUpstreamExtraParams,
        )

        d = dict(src_dict)
        _inbound_protos = d.pop("inbound_protos", UNSET)
        inbound_protos: list[ModelServiceGatewayConfigInboundProtosItem] | Unset = UNSET
        if _inbound_protos is not UNSET:
            inbound_protos = []
            for inbound_protos_item_data in _inbound_protos:
                inbound_protos_item = ModelServiceGatewayConfigInboundProtosItem(
                    inbound_protos_item_data
                )

                inbound_protos.append(inbound_protos_item)

        _upstream_extra_params = d.pop("upstream_extra_params", UNSET)
        upstream_extra_params: ModelServiceGatewayConfigUpstreamExtraParams | Unset
        if isinstance(_upstream_extra_params, Unset):
            upstream_extra_params = UNSET
        else:
            upstream_extra_params = (
                ModelServiceGatewayConfigUpstreamExtraParams.from_dict(
                    _upstream_extra_params
                )
            )

        model_service_gateway_config = cls(
            inbound_protos=inbound_protos,
            upstream_extra_params=upstream_extra_params,
        )

        return model_service_gateway_config
