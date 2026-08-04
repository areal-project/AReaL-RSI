from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="ModelServiceAISClusterConfig")


@_attrs_define
class ModelServiceAISClusterConfig:
    """
    Attributes:
        app_name (str): AIS app name. Also used as the cluster selector value.
        gpu_type (str):  Default: 'h200'.
        enabled (bool):  Default: True.
        group_id (int | Unset):
    """

    app_name: str
    gpu_type: str = "h200"
    enabled: bool = True
    group_id: int | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        app_name = self.app_name

        gpu_type = self.gpu_type

        enabled = self.enabled

        group_id = self.group_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "app_name": app_name,
                "gpu_type": gpu_type,
                "enabled": enabled,
            }
        )
        if group_id is not UNSET:
            field_dict["group_id"] = group_id

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        app_name = d.pop("app_name")

        gpu_type = d.pop("gpu_type")

        enabled = d.pop("enabled")

        group_id = d.pop("group_id", UNSET)

        model_service_ais_cluster_config = cls(
            app_name=app_name,
            gpu_type=gpu_type,
            enabled=enabled,
            group_id=group_id,
        )

        return model_service_ais_cluster_config
