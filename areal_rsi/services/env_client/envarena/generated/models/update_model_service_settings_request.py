from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_service_ais_cluster_config import ModelServiceAISClusterConfig
    from ..models.model_service_ais_runtime_config import ModelServiceAISRuntimeConfig


T = TypeVar("T", bound="UpdateModelServiceSettingsRequest")


@_attrs_define
class UpdateModelServiceSettingsRequest:
    """
    Attributes:
        enabled (bool):
        callback_base_url (str):
        bridge_url (str):  Example: http://127.0.0.1:8090.
        bridge_timeout (str):  Example: 5m.
        endpoint_id_prefix (str):
        sglang_log_dir (str):
        ais (ModelServiceAISRuntimeConfig):
        ais_clusters (list[ModelServiceAISClusterConfig]):
        register_token (str | Unset): New model-service callback register token. Omit to keep the current token.
        bridge_token (str | Unset): New Go-side bridge bearer token. Omit to keep the current token.
    """

    enabled: bool
    callback_base_url: str
    bridge_url: str
    bridge_timeout: str
    endpoint_id_prefix: str
    sglang_log_dir: str
    ais: ModelServiceAISRuntimeConfig
    ais_clusters: list[ModelServiceAISClusterConfig]
    register_token: str | Unset = UNSET
    bridge_token: str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        enabled = self.enabled

        callback_base_url = self.callback_base_url

        bridge_url = self.bridge_url

        bridge_timeout = self.bridge_timeout

        endpoint_id_prefix = self.endpoint_id_prefix

        sglang_log_dir = self.sglang_log_dir

        ais = self.ais.to_dict()

        ais_clusters = []
        for ais_clusters_item_data in self.ais_clusters:
            ais_clusters_item = ais_clusters_item_data.to_dict()
            ais_clusters.append(ais_clusters_item)

        register_token = self.register_token

        bridge_token = self.bridge_token

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "enabled": enabled,
                "callback_base_url": callback_base_url,
                "bridge_url": bridge_url,
                "bridge_timeout": bridge_timeout,
                "endpoint_id_prefix": endpoint_id_prefix,
                "sglang_log_dir": sglang_log_dir,
                "ais": ais,
                "ais_clusters": ais_clusters,
            }
        )
        if register_token is not UNSET:
            field_dict["register_token"] = register_token
        if bridge_token is not UNSET:
            field_dict["bridge_token"] = bridge_token

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.model_service_ais_cluster_config import (
            ModelServiceAISClusterConfig,
        )
        from ..models.model_service_ais_runtime_config import (
            ModelServiceAISRuntimeConfig,
        )

        d = dict(src_dict)
        enabled = d.pop("enabled")

        callback_base_url = d.pop("callback_base_url")

        bridge_url = d.pop("bridge_url")

        bridge_timeout = d.pop("bridge_timeout")

        endpoint_id_prefix = d.pop("endpoint_id_prefix")

        sglang_log_dir = d.pop("sglang_log_dir")

        ais = ModelServiceAISRuntimeConfig.from_dict(d.pop("ais"))

        ais_clusters = []
        _ais_clusters = d.pop("ais_clusters")
        for ais_clusters_item_data in _ais_clusters:
            ais_clusters_item = ModelServiceAISClusterConfig.from_dict(
                ais_clusters_item_data
            )

            ais_clusters.append(ais_clusters_item)

        register_token = d.pop("register_token", UNSET)

        bridge_token = d.pop("bridge_token", UNSET)

        update_model_service_settings_request = cls(
            enabled=enabled,
            callback_base_url=callback_base_url,
            bridge_url=bridge_url,
            bridge_timeout=bridge_timeout,
            endpoint_id_prefix=endpoint_id_prefix,
            sglang_log_dir=sglang_log_dir,
            ais=ais,
            ais_clusters=ais_clusters,
            register_token=register_token,
            bridge_token=bridge_token,
        )

        return update_model_service_settings_request
