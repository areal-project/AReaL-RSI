from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="ModelServiceInstanceStartedRequest")


@_attrs_define
class ModelServiceInstanceStartedRequest:
    """
    Attributes:
        service_id (str):
        instance_id (str):
        pod_ip (str | Unset):
        message (str | Unset):
    """

    service_id: str
    instance_id: str
    pod_ip: str | Unset = UNSET
    message: str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        service_id = self.service_id

        instance_id = self.instance_id

        pod_ip = self.pod_ip

        message = self.message

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "service_id": service_id,
                "instance_id": instance_id,
            }
        )
        if pod_ip is not UNSET:
            field_dict["pod_ip"] = pod_ip
        if message is not UNSET:
            field_dict["message"] = message

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        service_id = d.pop("service_id")

        instance_id = d.pop("instance_id")

        pod_ip = d.pop("pod_ip", UNSET)

        message = d.pop("message", UNSET)

        model_service_instance_started_request = cls(
            service_id=service_id,
            instance_id=instance_id,
            pod_ip=pod_ip,
            message=message,
        )

        return model_service_instance_started_request
