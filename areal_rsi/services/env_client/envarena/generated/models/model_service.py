from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.model_service_status import ModelServiceStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_service_instance import ModelServiceInstance
    from ..models.model_service_metadata import ModelServiceMetadata


T = TypeVar("T", bound="ModelService")


@_attrs_define
class ModelService:
    """
    Attributes:
        service_id (str):
        served_model_name (str):
        replicas (int):
        status (ModelServiceStatus):
        created_at (datetime.datetime):
        updated_at (datetime.datetime):
        ready_replicas (int):
        instances (list[ModelServiceInstance]):
        metadata (ModelServiceMetadata | Unset):
        created_by (str | Unset):
    """

    service_id: str
    served_model_name: str
    replicas: int
    status: ModelServiceStatus
    created_at: datetime.datetime
    updated_at: datetime.datetime
    ready_replicas: int
    instances: list[ModelServiceInstance]
    metadata: ModelServiceMetadata | Unset = UNSET
    created_by: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        service_id = self.service_id

        served_model_name = self.served_model_name

        replicas = self.replicas

        status = self.status.value

        created_at = self.created_at.isoformat()

        updated_at = self.updated_at.isoformat()

        ready_replicas = self.ready_replicas

        instances = []
        for instances_item_data in self.instances:
            instances_item = instances_item_data.to_dict()
            instances.append(instances_item)

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

        created_by = self.created_by

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "service_id": service_id,
                "served_model_name": served_model_name,
                "replicas": replicas,
                "status": status,
                "created_at": created_at,
                "updated_at": updated_at,
                "ready_replicas": ready_replicas,
                "instances": instances,
            }
        )
        if metadata is not UNSET:
            field_dict["metadata"] = metadata
        if created_by is not UNSET:
            field_dict["created_by"] = created_by

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.model_service_instance import ModelServiceInstance
        from ..models.model_service_metadata import ModelServiceMetadata

        d = dict(src_dict)
        service_id = d.pop("service_id")

        served_model_name = d.pop("served_model_name")

        replicas = d.pop("replicas")

        status = ModelServiceStatus(d.pop("status"))

        created_at = isoparse(d.pop("created_at"))

        updated_at = isoparse(d.pop("updated_at"))

        ready_replicas = d.pop("ready_replicas")

        instances = []
        _instances = d.pop("instances")
        for instances_item_data in _instances:
            instances_item = ModelServiceInstance.from_dict(instances_item_data)

            instances.append(instances_item)

        _metadata = d.pop("metadata", UNSET)
        metadata: ModelServiceMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = ModelServiceMetadata.from_dict(_metadata)

        created_by = d.pop("created_by", UNSET)

        model_service = cls(
            service_id=service_id,
            served_model_name=served_model_name,
            replicas=replicas,
            status=status,
            created_at=created_at,
            updated_at=updated_at,
            ready_replicas=ready_replicas,
            instances=instances,
            metadata=metadata,
            created_by=created_by,
        )

        model_service.additional_properties = d
        return model_service

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
