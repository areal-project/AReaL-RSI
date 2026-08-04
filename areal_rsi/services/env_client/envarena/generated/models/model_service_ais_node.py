from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_service_ais_node_raw import ModelServiceAISNodeRaw


T = TypeVar("T", bound="ModelServiceAISNode")


@_attrs_define
class ModelServiceAISNode:
    """
    Attributes:
        index (int | Unset):
        role (str | Unset):
        name (str | Unset):
        status (str | Unset):
        evicted (bool | Unset):
        pod_ip (str | Unset):
        host_ip (str | Unset):
        log_url (str | Unset):
        trace_url (str | Unset):
        raw (ModelServiceAISNodeRaw | Unset):
    """

    index: int | Unset = UNSET
    role: str | Unset = UNSET
    name: str | Unset = UNSET
    status: str | Unset = UNSET
    evicted: bool | Unset = UNSET
    pod_ip: str | Unset = UNSET
    host_ip: str | Unset = UNSET
    log_url: str | Unset = UNSET
    trace_url: str | Unset = UNSET
    raw: ModelServiceAISNodeRaw | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        index = self.index

        role = self.role

        name = self.name

        status = self.status

        evicted = self.evicted

        pod_ip = self.pod_ip

        host_ip = self.host_ip

        log_url = self.log_url

        trace_url = self.trace_url

        raw: dict[str, Any] | Unset = UNSET
        if not isinstance(self.raw, Unset):
            raw = self.raw.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if index is not UNSET:
            field_dict["index"] = index
        if role is not UNSET:
            field_dict["role"] = role
        if name is not UNSET:
            field_dict["name"] = name
        if status is not UNSET:
            field_dict["status"] = status
        if evicted is not UNSET:
            field_dict["evicted"] = evicted
        if pod_ip is not UNSET:
            field_dict["pod_ip"] = pod_ip
        if host_ip is not UNSET:
            field_dict["host_ip"] = host_ip
        if log_url is not UNSET:
            field_dict["log_url"] = log_url
        if trace_url is not UNSET:
            field_dict["trace_url"] = trace_url
        if raw is not UNSET:
            field_dict["raw"] = raw

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.model_service_ais_node_raw import ModelServiceAISNodeRaw

        d = dict(src_dict)
        index = d.pop("index", UNSET)

        role = d.pop("role", UNSET)

        name = d.pop("name", UNSET)

        status = d.pop("status", UNSET)

        evicted = d.pop("evicted", UNSET)

        pod_ip = d.pop("pod_ip", UNSET)

        host_ip = d.pop("host_ip", UNSET)

        log_url = d.pop("log_url", UNSET)

        trace_url = d.pop("trace_url", UNSET)

        _raw = d.pop("raw", UNSET)
        raw: ModelServiceAISNodeRaw | Unset
        if isinstance(_raw, Unset):
            raw = UNSET
        else:
            raw = ModelServiceAISNodeRaw.from_dict(_raw)

        model_service_ais_node = cls(
            index=index,
            role=role,
            name=name,
            status=status,
            evicted=evicted,
            pod_ip=pod_ip,
            host_ip=host_ip,
            log_url=log_url,
            trace_url=trace_url,
            raw=raw,
        )

        model_service_ais_node.additional_properties = d
        return model_service_ais_node

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
