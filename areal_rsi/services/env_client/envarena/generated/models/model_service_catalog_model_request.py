from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_service_catalog_model_request_metadata import (
        ModelServiceCatalogModelRequestMetadata,
    )
    from ..models.model_service_gateway_config import ModelServiceGatewayConfig
    from ..models.model_service_resource_spec import ModelServiceResourceSpec
    from ..models.model_service_sglang_spec import ModelServiceSglangSpec


T = TypeVar("T", bound="ModelServiceCatalogModelRequest")


@_attrs_define
class ModelServiceCatalogModelRequest:
    """
    Attributes:
        model_id (str):
        model_name (str):
        model_path (str):
        family (str | Unset):
        parameter_count (int | Unset):
        parameter_size (str | Unset):
        image (str | Unset):
        resource_spec (ModelServiceResourceSpec | Unset):
        sglang (ModelServiceSglangSpec | Unset):
        gateway (ModelServiceGatewayConfig | Unset):
        source (str | Unset):
        metadata (ModelServiceCatalogModelRequestMetadata | Unset):
    """

    model_id: str
    model_name: str
    model_path: str
    family: str | Unset = UNSET
    parameter_count: int | Unset = UNSET
    parameter_size: str | Unset = UNSET
    image: str | Unset = UNSET
    resource_spec: ModelServiceResourceSpec | Unset = UNSET
    sglang: ModelServiceSglangSpec | Unset = UNSET
    gateway: ModelServiceGatewayConfig | Unset = UNSET
    source: str | Unset = UNSET
    metadata: ModelServiceCatalogModelRequestMetadata | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        model_id = self.model_id

        model_name = self.model_name

        model_path = self.model_path

        family = self.family

        parameter_count = self.parameter_count

        parameter_size = self.parameter_size

        image = self.image

        resource_spec: dict[str, Any] | Unset = UNSET
        if not isinstance(self.resource_spec, Unset):
            resource_spec = self.resource_spec.to_dict()

        sglang: dict[str, Any] | Unset = UNSET
        if not isinstance(self.sglang, Unset):
            sglang = self.sglang.to_dict()

        gateway: dict[str, Any] | Unset = UNSET
        if not isinstance(self.gateway, Unset):
            gateway = self.gateway.to_dict()

        source = self.source

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "model_id": model_id,
                "model_name": model_name,
                "model_path": model_path,
            }
        )
        if family is not UNSET:
            field_dict["family"] = family
        if parameter_count is not UNSET:
            field_dict["parameter_count"] = parameter_count
        if parameter_size is not UNSET:
            field_dict["parameter_size"] = parameter_size
        if image is not UNSET:
            field_dict["image"] = image
        if resource_spec is not UNSET:
            field_dict["resource_spec"] = resource_spec
        if sglang is not UNSET:
            field_dict["sglang"] = sglang
        if gateway is not UNSET:
            field_dict["gateway"] = gateway
        if source is not UNSET:
            field_dict["source"] = source
        if metadata is not UNSET:
            field_dict["metadata"] = metadata

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.model_service_catalog_model_request_metadata import (
            ModelServiceCatalogModelRequestMetadata,
        )
        from ..models.model_service_gateway_config import ModelServiceGatewayConfig
        from ..models.model_service_resource_spec import ModelServiceResourceSpec
        from ..models.model_service_sglang_spec import ModelServiceSglangSpec

        d = dict(src_dict)
        model_id = d.pop("model_id")

        model_name = d.pop("model_name")

        model_path = d.pop("model_path")

        family = d.pop("family", UNSET)

        parameter_count = d.pop("parameter_count", UNSET)

        parameter_size = d.pop("parameter_size", UNSET)

        image = d.pop("image", UNSET)

        _resource_spec = d.pop("resource_spec", UNSET)
        resource_spec: ModelServiceResourceSpec | Unset
        if isinstance(_resource_spec, Unset):
            resource_spec = UNSET
        else:
            resource_spec = ModelServiceResourceSpec.from_dict(_resource_spec)

        _sglang = d.pop("sglang", UNSET)
        sglang: ModelServiceSglangSpec | Unset
        if isinstance(_sglang, Unset):
            sglang = UNSET
        else:
            sglang = ModelServiceSglangSpec.from_dict(_sglang)

        _gateway = d.pop("gateway", UNSET)
        gateway: ModelServiceGatewayConfig | Unset
        if isinstance(_gateway, Unset):
            gateway = UNSET
        else:
            gateway = ModelServiceGatewayConfig.from_dict(_gateway)

        source = d.pop("source", UNSET)

        _metadata = d.pop("metadata", UNSET)
        metadata: ModelServiceCatalogModelRequestMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = ModelServiceCatalogModelRequestMetadata.from_dict(_metadata)

        model_service_catalog_model_request = cls(
            model_id=model_id,
            model_name=model_name,
            model_path=model_path,
            family=family,
            parameter_count=parameter_count,
            parameter_size=parameter_size,
            image=image,
            resource_spec=resource_spec,
            sglang=sglang,
            gateway=gateway,
            source=source,
            metadata=metadata,
        )

        return model_service_catalog_model_request
