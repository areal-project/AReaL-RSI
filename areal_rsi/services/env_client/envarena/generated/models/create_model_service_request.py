from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.create_model_service_request_metadata import (
        CreateModelServiceRequestMetadata,
    )
    from ..models.model_service_gateway_config import ModelServiceGatewayConfig
    from ..models.model_service_resource_spec import ModelServiceResourceSpec
    from ..models.model_service_sglang_spec import ModelServiceSglangSpec


T = TypeVar("T", bound="CreateModelServiceRequest")


@_attrs_define
class CreateModelServiceRequest:
    """
    Attributes:
        served_model_name (str): Model alias exposed through EnvArena LLM Gateway after an instance becomes READY.
            Example: qwen3-coder.
        model_path (str): Filesystem model path visible inside the AIS job. Example:
            /storage/openpsi/models/Qwen__Qwen3-Coder.
        image (str | Unset): Optional AIS runtime image.
        replicas (int | Unset):  Default: 1.
        resource_spec (ModelServiceResourceSpec | Unset):
        sglang (ModelServiceSglangSpec | Unset):
        gateway (ModelServiceGatewayConfig | Unset):
        metadata (CreateModelServiceRequestMetadata | Unset):
    """

    served_model_name: str
    model_path: str
    image: str | Unset = UNSET
    replicas: int | Unset = 1
    resource_spec: ModelServiceResourceSpec | Unset = UNSET
    sglang: ModelServiceSglangSpec | Unset = UNSET
    gateway: ModelServiceGatewayConfig | Unset = UNSET
    metadata: CreateModelServiceRequestMetadata | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        served_model_name = self.served_model_name

        model_path = self.model_path

        image = self.image

        replicas = self.replicas

        resource_spec: dict[str, Any] | Unset = UNSET
        if not isinstance(self.resource_spec, Unset):
            resource_spec = self.resource_spec.to_dict()

        sglang: dict[str, Any] | Unset = UNSET
        if not isinstance(self.sglang, Unset):
            sglang = self.sglang.to_dict()

        gateway: dict[str, Any] | Unset = UNSET
        if not isinstance(self.gateway, Unset):
            gateway = self.gateway.to_dict()

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "served_model_name": served_model_name,
                "model_path": model_path,
            }
        )
        if image is not UNSET:
            field_dict["image"] = image
        if replicas is not UNSET:
            field_dict["replicas"] = replicas
        if resource_spec is not UNSET:
            field_dict["resource_spec"] = resource_spec
        if sglang is not UNSET:
            field_dict["sglang"] = sglang
        if gateway is not UNSET:
            field_dict["gateway"] = gateway
        if metadata is not UNSET:
            field_dict["metadata"] = metadata

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.create_model_service_request_metadata import (
            CreateModelServiceRequestMetadata,
        )
        from ..models.model_service_gateway_config import ModelServiceGatewayConfig
        from ..models.model_service_resource_spec import ModelServiceResourceSpec
        from ..models.model_service_sglang_spec import ModelServiceSglangSpec

        d = dict(src_dict)
        served_model_name = d.pop("served_model_name")

        model_path = d.pop("model_path")

        image = d.pop("image", UNSET)

        replicas = d.pop("replicas", UNSET)

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

        _metadata = d.pop("metadata", UNSET)
        metadata: CreateModelServiceRequestMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = CreateModelServiceRequestMetadata.from_dict(_metadata)

        create_model_service_request = cls(
            served_model_name=served_model_name,
            model_path=model_path,
            image=image,
            replicas=replicas,
            resource_spec=resource_spec,
            sglang=sglang,
            gateway=gateway,
            metadata=metadata,
        )

        return create_model_service_request
