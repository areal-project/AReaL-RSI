from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.model_service_catalog_model_availability_status import (
    ModelServiceCatalogModelAvailabilityStatus,
)
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_service_catalog_model_metadata import (
        ModelServiceCatalogModelMetadata,
    )
    from ..models.model_service_gateway_config import ModelServiceGatewayConfig
    from ..models.model_service_resource_spec import ModelServiceResourceSpec
    from ..models.model_service_sglang_spec import ModelServiceSglangSpec


T = TypeVar("T", bound="ModelServiceCatalogModel")


@_attrs_define
class ModelServiceCatalogModel:
    """
    Attributes:
        model_id (str):
        model_name (str):
        model_path (str):
        gateway (ModelServiceGatewayConfig):
        exists (bool):
        readable (bool):
        weights_exist (bool):
        weights_readable (bool):
        availability_status (ModelServiceCatalogModelAvailabilityStatus):
        created_at (datetime.datetime):
        updated_at (datetime.datetime):
        family (str | Unset):
        parameter_count (int | Unset):
        parameter_size (str | Unset):
        image (str | Unset):
        resource_spec (ModelServiceResourceSpec | Unset):
        sglang (ModelServiceSglangSpec | Unset):
        source (str | Unset):
        metadata (ModelServiceCatalogModelMetadata | Unset):
        created_by (str | Unset):
    """

    model_id: str
    model_name: str
    model_path: str
    gateway: ModelServiceGatewayConfig
    exists: bool
    readable: bool
    weights_exist: bool
    weights_readable: bool
    availability_status: ModelServiceCatalogModelAvailabilityStatus
    created_at: datetime.datetime
    updated_at: datetime.datetime
    family: str | Unset = UNSET
    parameter_count: int | Unset = UNSET
    parameter_size: str | Unset = UNSET
    image: str | Unset = UNSET
    resource_spec: ModelServiceResourceSpec | Unset = UNSET
    sglang: ModelServiceSglangSpec | Unset = UNSET
    source: str | Unset = UNSET
    metadata: ModelServiceCatalogModelMetadata | Unset = UNSET
    created_by: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        model_id = self.model_id

        model_name = self.model_name

        model_path = self.model_path

        gateway = self.gateway.to_dict()

        exists = self.exists

        readable = self.readable

        weights_exist = self.weights_exist

        weights_readable = self.weights_readable

        availability_status = self.availability_status.value

        created_at = self.created_at.isoformat()

        updated_at = self.updated_at.isoformat()

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

        source = self.source

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

        created_by = self.created_by

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "model_id": model_id,
                "model_name": model_name,
                "model_path": model_path,
                "gateway": gateway,
                "exists": exists,
                "readable": readable,
                "weights_exist": weights_exist,
                "weights_readable": weights_readable,
                "availability_status": availability_status,
                "created_at": created_at,
                "updated_at": updated_at,
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
        if source is not UNSET:
            field_dict["source"] = source
        if metadata is not UNSET:
            field_dict["metadata"] = metadata
        if created_by is not UNSET:
            field_dict["created_by"] = created_by

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.model_service_catalog_model_metadata import (
            ModelServiceCatalogModelMetadata,
        )
        from ..models.model_service_gateway_config import ModelServiceGatewayConfig
        from ..models.model_service_resource_spec import ModelServiceResourceSpec
        from ..models.model_service_sglang_spec import ModelServiceSglangSpec

        d = dict(src_dict)
        model_id = d.pop("model_id")

        model_name = d.pop("model_name")

        model_path = d.pop("model_path")

        gateway = ModelServiceGatewayConfig.from_dict(d.pop("gateway"))

        exists = d.pop("exists")

        readable = d.pop("readable")

        weights_exist = d.pop("weights_exist")

        weights_readable = d.pop("weights_readable")

        availability_status = ModelServiceCatalogModelAvailabilityStatus(
            d.pop("availability_status")
        )

        created_at = isoparse(d.pop("created_at"))

        updated_at = isoparse(d.pop("updated_at"))

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

        source = d.pop("source", UNSET)

        _metadata = d.pop("metadata", UNSET)
        metadata: ModelServiceCatalogModelMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = ModelServiceCatalogModelMetadata.from_dict(_metadata)

        created_by = d.pop("created_by", UNSET)

        model_service_catalog_model = cls(
            model_id=model_id,
            model_name=model_name,
            model_path=model_path,
            gateway=gateway,
            exists=exists,
            readable=readable,
            weights_exist=weights_exist,
            weights_readable=weights_readable,
            availability_status=availability_status,
            created_at=created_at,
            updated_at=updated_at,
            family=family,
            parameter_count=parameter_count,
            parameter_size=parameter_size,
            image=image,
            resource_spec=resource_spec,
            sglang=sglang,
            source=source,
            metadata=metadata,
            created_by=created_by,
        )

        model_service_catalog_model.additional_properties = d
        return model_service_catalog_model

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
