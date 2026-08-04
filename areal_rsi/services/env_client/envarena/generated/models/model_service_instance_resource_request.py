from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.model_service_resource_spec import ModelServiceResourceSpec


T = TypeVar("T", bound="ModelServiceInstanceResourceRequest")


@_attrs_define
class ModelServiceInstanceResourceRequest:
    """
    Attributes:
        resource_spec (ModelServiceResourceSpec):
        nodes (int):
        total_gpus (int):
        total_cpu (int):
        total_memory (int):
        total_disk_m (int):
        host_ports (int):
    """

    resource_spec: ModelServiceResourceSpec
    nodes: int
    total_gpus: int
    total_cpu: int
    total_memory: int
    total_disk_m: int
    host_ports: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        resource_spec = self.resource_spec.to_dict()

        nodes = self.nodes

        total_gpus = self.total_gpus

        total_cpu = self.total_cpu

        total_memory = self.total_memory

        total_disk_m = self.total_disk_m

        host_ports = self.host_ports

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "resource_spec": resource_spec,
                "nodes": nodes,
                "total_gpus": total_gpus,
                "total_cpu": total_cpu,
                "total_memory": total_memory,
                "total_disk_m": total_disk_m,
                "host_ports": host_ports,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.model_service_resource_spec import ModelServiceResourceSpec

        d = dict(src_dict)
        resource_spec = ModelServiceResourceSpec.from_dict(d.pop("resource_spec"))

        nodes = d.pop("nodes")

        total_gpus = d.pop("total_gpus")

        total_cpu = d.pop("total_cpu")

        total_memory = d.pop("total_memory")

        total_disk_m = d.pop("total_disk_m")

        host_ports = d.pop("host_ports")

        model_service_instance_resource_request = cls(
            resource_spec=resource_spec,
            nodes=nodes,
            total_gpus=total_gpus,
            total_cpu=total_cpu,
            total_memory=total_memory,
            total_disk_m=total_disk_m,
            host_ports=host_ports,
        )

        model_service_instance_resource_request.additional_properties = d
        return model_service_instance_resource_request

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
