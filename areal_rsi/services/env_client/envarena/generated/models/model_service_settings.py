from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_service_ais_cluster_config import ModelServiceAISClusterConfig
    from ..models.model_service_ais_runtime_config import ModelServiceAISRuntimeConfig


T = TypeVar("T", bound="ModelServiceSettings")


@_attrs_define
class ModelServiceSettings:
    """
    Attributes:
        enabled (bool):
        callback_base_url (str):
        register_token_configured (bool): Whether the model-service callback register token is configured.
        bridge_url (str):  Example: http://127.0.0.1:8090.
        bridge_token_configured (bool): Whether the Go-side bridge bearer token is configured.
        bridge_timeout (str):  Example: 5m.
        endpoint_id_prefix (str):  Example: model-service-.
        sglang_log_dir (str):
        ais (ModelServiceAISRuntimeConfig):
        ais_clusters (list[ModelServiceAISClusterConfig]):
        register_token_hint (str | Unset):
        bridge_token_hint (str | Unset):
    """

    enabled: bool
    callback_base_url: str
    register_token_configured: bool
    bridge_url: str
    bridge_token_configured: bool
    bridge_timeout: str
    endpoint_id_prefix: str
    sglang_log_dir: str
    ais: ModelServiceAISRuntimeConfig
    ais_clusters: list[ModelServiceAISClusterConfig]
    register_token_hint: str | Unset = UNSET
    bridge_token_hint: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        enabled = self.enabled

        callback_base_url = self.callback_base_url

        register_token_configured = self.register_token_configured

        bridge_url = self.bridge_url

        bridge_token_configured = self.bridge_token_configured

        bridge_timeout = self.bridge_timeout

        endpoint_id_prefix = self.endpoint_id_prefix

        sglang_log_dir = self.sglang_log_dir

        ais = self.ais.to_dict()

        ais_clusters = []
        for ais_clusters_item_data in self.ais_clusters:
            ais_clusters_item = ais_clusters_item_data.to_dict()
            ais_clusters.append(ais_clusters_item)

        register_token_hint = self.register_token_hint

        bridge_token_hint = self.bridge_token_hint

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "enabled": enabled,
                "callback_base_url": callback_base_url,
                "register_token_configured": register_token_configured,
                "bridge_url": bridge_url,
                "bridge_token_configured": bridge_token_configured,
                "bridge_timeout": bridge_timeout,
                "endpoint_id_prefix": endpoint_id_prefix,
                "sglang_log_dir": sglang_log_dir,
                "ais": ais,
                "ais_clusters": ais_clusters,
            }
        )
        if register_token_hint is not UNSET:
            field_dict["register_token_hint"] = register_token_hint
        if bridge_token_hint is not UNSET:
            field_dict["bridge_token_hint"] = bridge_token_hint

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

        register_token_configured = d.pop("register_token_configured")

        bridge_url = d.pop("bridge_url")

        bridge_token_configured = d.pop("bridge_token_configured")

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

        register_token_hint = d.pop("register_token_hint", UNSET)

        bridge_token_hint = d.pop("bridge_token_hint", UNSET)

        model_service_settings = cls(
            enabled=enabled,
            callback_base_url=callback_base_url,
            register_token_configured=register_token_configured,
            bridge_url=bridge_url,
            bridge_token_configured=bridge_token_configured,
            bridge_timeout=bridge_timeout,
            endpoint_id_prefix=endpoint_id_prefix,
            sglang_log_dir=sglang_log_dir,
            ais=ais,
            ais_clusters=ais_clusters,
            register_token_hint=register_token_hint,
            bridge_token_hint=bridge_token_hint,
        )

        model_service_settings.additional_properties = d
        return model_service_settings

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
