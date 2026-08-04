from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="ModelServiceRegisterInstanceRequest")


@_attrs_define
class ModelServiceRegisterInstanceRequest:
    """
    Attributes:
        service_id (str):
        instance_id (str):
        pod_ip (str):
        port (int):
        served_model_name (str | Unset):
        model_path (str | Unset):
        health_url (str | Unset):
    """

    service_id: str
    instance_id: str
    pod_ip: str
    port: int
    served_model_name: str | Unset = UNSET
    model_path: str | Unset = UNSET
    health_url: str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        service_id = self.service_id

        instance_id = self.instance_id

        pod_ip = self.pod_ip

        port = self.port

        served_model_name = self.served_model_name

        model_path = self.model_path

        health_url = self.health_url

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "service_id": service_id,
                "instance_id": instance_id,
                "pod_ip": pod_ip,
                "port": port,
            }
        )
        if served_model_name is not UNSET:
            field_dict["served_model_name"] = served_model_name
        if model_path is not UNSET:
            field_dict["model_path"] = model_path
        if health_url is not UNSET:
            field_dict["health_url"] = health_url

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        service_id = d.pop("service_id")

        instance_id = d.pop("instance_id")

        pod_ip = d.pop("pod_ip")

        port = d.pop("port")

        served_model_name = d.pop("served_model_name", UNSET)

        model_path = d.pop("model_path", UNSET)

        health_url = d.pop("health_url", UNSET)

        model_service_register_instance_request = cls(
            service_id=service_id,
            instance_id=instance_id,
            pod_ip=pod_ip,
            port=port,
            served_model_name=served_model_name,
            model_path=model_path,
            health_url=health_url,
        )

        return model_service_register_instance_request
