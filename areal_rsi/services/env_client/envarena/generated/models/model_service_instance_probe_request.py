from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define

from ..models.model_service_instance_probe_request_protocol import (
    ModelServiceInstanceProbeRequestProtocol,
)
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_service_instance_probe_request_payload import (
        ModelServiceInstanceProbeRequestPayload,
    )


T = TypeVar("T", bound="ModelServiceInstanceProbeRequest")


@_attrs_define
class ModelServiceInstanceProbeRequest:
    """
    Attributes:
        protocol (ModelServiceInstanceProbeRequestProtocol):
        payload (ModelServiceInstanceProbeRequestPayload):
        timeout (int | Unset):  Default: 30.
    """

    protocol: ModelServiceInstanceProbeRequestProtocol
    payload: ModelServiceInstanceProbeRequestPayload
    timeout: int | Unset = 30

    def to_dict(self) -> dict[str, Any]:
        protocol = self.protocol.value

        payload = self.payload.to_dict()

        timeout = self.timeout

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "protocol": protocol,
                "payload": payload,
            }
        )
        if timeout is not UNSET:
            field_dict["timeout"] = timeout

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.model_service_instance_probe_request_payload import (
            ModelServiceInstanceProbeRequestPayload,
        )

        d = dict(src_dict)
        protocol = ModelServiceInstanceProbeRequestProtocol(d.pop("protocol"))

        payload = ModelServiceInstanceProbeRequestPayload.from_dict(d.pop("payload"))

        timeout = d.pop("timeout", UNSET)

        model_service_instance_probe_request = cls(
            protocol=protocol,
            payload=payload,
            timeout=timeout,
        )

        return model_service_instance_probe_request
