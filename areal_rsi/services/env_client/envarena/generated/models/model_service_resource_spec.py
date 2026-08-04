from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="ModelServiceResourceSpec")


@_attrs_define
class ModelServiceResourceSpec:
    """
    Attributes:
        gpu_num (int | Unset):  Default: 8.
        gpu_type (str | Unset):  Default: 'h200'.
        ais_cluster (str | Unset): AIS cluster selector. This is the configured AIS app_name.
        node_num (int | Unset):  Default: 1.
        cpu (int | Unset):  Default: 96.
        memory (int | Unset): AIS memory request value. Default: 500000.
        disk_m (int | Unset):  Default: 1000000.
        host_port (int | Unset):  Default: 1.
    """

    gpu_num: int | Unset = 8
    gpu_type: str | Unset = "h200"
    ais_cluster: str | Unset = UNSET
    node_num: int | Unset = 1
    cpu: int | Unset = 96
    memory: int | Unset = 500000
    disk_m: int | Unset = 1000000
    host_port: int | Unset = 1

    def to_dict(self) -> dict[str, Any]:
        gpu_num = self.gpu_num

        gpu_type = self.gpu_type

        ais_cluster = self.ais_cluster

        node_num = self.node_num

        cpu = self.cpu

        memory = self.memory

        disk_m = self.disk_m

        host_port = self.host_port

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if gpu_num is not UNSET:
            field_dict["gpu_num"] = gpu_num
        if gpu_type is not UNSET:
            field_dict["gpu_type"] = gpu_type
        if ais_cluster is not UNSET:
            field_dict["ais_cluster"] = ais_cluster
        if node_num is not UNSET:
            field_dict["node_num"] = node_num
        if cpu is not UNSET:
            field_dict["cpu"] = cpu
        if memory is not UNSET:
            field_dict["memory"] = memory
        if disk_m is not UNSET:
            field_dict["disk_m"] = disk_m
        if host_port is not UNSET:
            field_dict["host_port"] = host_port

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        gpu_num = d.pop("gpu_num", UNSET)

        gpu_type = d.pop("gpu_type", UNSET)

        ais_cluster = d.pop("ais_cluster", UNSET)

        node_num = d.pop("node_num", UNSET)

        cpu = d.pop("cpu", UNSET)

        memory = d.pop("memory", UNSET)

        disk_m = d.pop("disk_m", UNSET)

        host_port = d.pop("host_port", UNSET)

        model_service_resource_spec = cls(
            gpu_num=gpu_num,
            gpu_type=gpu_type,
            ais_cluster=ais_cluster,
            node_num=node_num,
            cpu=cpu,
            memory=memory,
            disk_m=disk_m,
            host_port=host_port,
        )

        return model_service_resource_spec
