from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_service import ModelService
    from ..models.model_service_ais_job import ModelServiceAISJob
    from ..models.model_service_endpoint import ModelServiceEndpoint
    from ..models.model_service_instance import ModelServiceInstance
    from ..models.model_service_instance_detail_ais_type_1 import (
        ModelServiceInstanceDetailAisType1,
    )
    from ..models.model_service_instance_resource_request import (
        ModelServiceInstanceResourceRequest,
    )


T = TypeVar("T", bound="ModelServiceInstanceDetail")


@_attrs_define
class ModelServiceInstanceDetail:
    """
    Attributes:
        service (ModelService):
        instance (ModelServiceInstance):
        endpoint (ModelServiceEndpoint):
        resource_request (ModelServiceInstanceResourceRequest):
        ais (ModelServiceAISJob | ModelServiceInstanceDetailAisType1 | Unset):
    """

    service: ModelService
    instance: ModelServiceInstance
    endpoint: ModelServiceEndpoint
    resource_request: ModelServiceInstanceResourceRequest
    ais: ModelServiceAISJob | ModelServiceInstanceDetailAisType1 | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.model_service_ais_job import ModelServiceAISJob

        service = self.service.to_dict()

        instance = self.instance.to_dict()

        endpoint = self.endpoint.to_dict()

        resource_request = self.resource_request.to_dict()

        ais: dict[str, Any] | Unset
        if isinstance(self.ais, Unset):
            ais = UNSET
        elif isinstance(self.ais, ModelServiceAISJob):
            ais = self.ais.to_dict()
        else:
            ais = self.ais.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "service": service,
                "instance": instance,
                "endpoint": endpoint,
                "resource_request": resource_request,
            }
        )
        if ais is not UNSET:
            field_dict["ais"] = ais

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.model_service import ModelService
        from ..models.model_service_ais_job import ModelServiceAISJob
        from ..models.model_service_endpoint import ModelServiceEndpoint
        from ..models.model_service_instance import ModelServiceInstance
        from ..models.model_service_instance_detail_ais_type_1 import (
            ModelServiceInstanceDetailAisType1,
        )
        from ..models.model_service_instance_resource_request import (
            ModelServiceInstanceResourceRequest,
        )

        d = dict(src_dict)
        service = ModelService.from_dict(d.pop("service"))

        instance = ModelServiceInstance.from_dict(d.pop("instance"))

        endpoint = ModelServiceEndpoint.from_dict(d.pop("endpoint"))

        resource_request = ModelServiceInstanceResourceRequest.from_dict(
            d.pop("resource_request")
        )

        def _parse_ais(
            data: object,
        ) -> ModelServiceAISJob | ModelServiceInstanceDetailAisType1 | Unset:
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                ais_type_0 = ModelServiceAISJob.from_dict(data)

                return ais_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            if not isinstance(data, dict):
                raise TypeError()
            ais_type_1 = ModelServiceInstanceDetailAisType1.from_dict(data)

            return ais_type_1

        ais = _parse_ais(d.pop("ais", UNSET))

        model_service_instance_detail = cls(
            service=service,
            instance=instance,
            endpoint=endpoint,
            resource_request=resource_request,
            ais=ais,
        )

        model_service_instance_detail.additional_properties = d
        return model_service_instance_detail

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
