from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.model_service_ais_cluster_config import ModelServiceAISClusterConfig


T = TypeVar("T", bound="ModelServiceAISClustersResponse")


@_attrs_define
class ModelServiceAISClustersResponse:
    """
    Attributes:
        clusters (list[ModelServiceAISClusterConfig]):
        ais_user_configured (bool): Whether the shared root AIS employee ID is configured in model-service runtime
            settings.
        ais_token_configured (bool): Whether the shared root AIS token is configured in model-service runtime settings.
    """

    clusters: list[ModelServiceAISClusterConfig]
    ais_user_configured: bool
    ais_token_configured: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        clusters = []
        for clusters_item_data in self.clusters:
            clusters_item = clusters_item_data.to_dict()
            clusters.append(clusters_item)

        ais_user_configured = self.ais_user_configured

        ais_token_configured = self.ais_token_configured

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "clusters": clusters,
                "ais_user_configured": ais_user_configured,
                "ais_token_configured": ais_token_configured,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.model_service_ais_cluster_config import (
            ModelServiceAISClusterConfig,
        )

        d = dict(src_dict)
        clusters = []
        _clusters = d.pop("clusters")
        for clusters_item_data in _clusters:
            clusters_item = ModelServiceAISClusterConfig.from_dict(clusters_item_data)

            clusters.append(clusters_item)

        ais_user_configured = d.pop("ais_user_configured")

        ais_token_configured = d.pop("ais_token_configured")

        model_service_ais_clusters_response = cls(
            clusters=clusters,
            ais_user_configured=ais_user_configured,
            ais_token_configured=ais_token_configured,
        )

        model_service_ais_clusters_response.additional_properties = d
        return model_service_ais_clusters_response

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
