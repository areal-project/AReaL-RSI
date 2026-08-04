from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.model_service_instance_probe_response_protocol import (
    ModelServiceInstanceProbeResponseProtocol,
)
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_service_instance_probe_response_headers import (
        ModelServiceInstanceProbeResponseHeaders,
    )


T = TypeVar("T", bound="ModelServiceInstanceProbeResponse")


@_attrs_define
class ModelServiceInstanceProbeResponse:
    """
    Attributes:
        ok (bool):
        status_code (int):
        protocol (ModelServiceInstanceProbeResponseProtocol):
        url (str):
        response (Any | Unset): Protocol-native upstream response, or plain text when the upstream did not return JSON.
        error (str | Unset):
        headers (ModelServiceInstanceProbeResponseHeaders | Unset):
    """

    ok: bool
    status_code: int
    protocol: ModelServiceInstanceProbeResponseProtocol
    url: str
    response: Any | Unset = UNSET
    error: str | Unset = UNSET
    headers: ModelServiceInstanceProbeResponseHeaders | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        ok = self.ok

        status_code = self.status_code

        protocol = self.protocol.value

        url = self.url

        response = self.response

        error = self.error

        headers: dict[str, Any] | Unset = UNSET
        if not isinstance(self.headers, Unset):
            headers = self.headers.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "ok": ok,
                "status_code": status_code,
                "protocol": protocol,
                "url": url,
            }
        )
        if response is not UNSET:
            field_dict["response"] = response
        if error is not UNSET:
            field_dict["error"] = error
        if headers is not UNSET:
            field_dict["headers"] = headers

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.model_service_instance_probe_response_headers import (
            ModelServiceInstanceProbeResponseHeaders,
        )

        d = dict(src_dict)
        ok = d.pop("ok")

        status_code = d.pop("status_code")

        protocol = ModelServiceInstanceProbeResponseProtocol(d.pop("protocol"))

        url = d.pop("url")

        response = d.pop("response", UNSET)

        error = d.pop("error", UNSET)

        _headers = d.pop("headers", UNSET)
        headers: ModelServiceInstanceProbeResponseHeaders | Unset
        if isinstance(_headers, Unset):
            headers = UNSET
        else:
            headers = ModelServiceInstanceProbeResponseHeaders.from_dict(_headers)

        model_service_instance_probe_response = cls(
            ok=ok,
            status_code=status_code,
            protocol=protocol,
            url=url,
            response=response,
            error=error,
            headers=headers,
        )

        model_service_instance_probe_response.additional_properties = d
        return model_service_instance_probe_response

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
