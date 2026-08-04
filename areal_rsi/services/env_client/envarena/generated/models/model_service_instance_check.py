from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.model_service_instance_check_object import ModelServiceInstanceCheckObject
from ..models.model_service_instance_check_overall_status import (
    ModelServiceInstanceCheckOverallStatus,
)
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_service import ModelService
    from ..models.model_service_endpoint import ModelServiceEndpoint
    from ..models.model_service_instance import ModelServiceInstance
    from ..models.model_service_instance_check_health import (
        ModelServiceInstanceCheckHealth,
    )
    from ..models.model_service_instance_check_item import ModelServiceInstanceCheckItem
    from ..models.model_service_instance_check_router_deployment import (
        ModelServiceInstanceCheckRouterDeployment,
    )
    from ..models.model_service_instance_logs import ModelServiceInstanceLogs


T = TypeVar("T", bound="ModelServiceInstanceCheck")


@_attrs_define
class ModelServiceInstanceCheck:
    """
    Attributes:
        object_ (ModelServiceInstanceCheckObject):
        service_id (str):
        instance_id (str):
        checked_at (datetime.datetime):
        overall_status (ModelServiceInstanceCheckOverallStatus):
        service (ModelService):
        instance (ModelServiceInstance):
        endpoint (ModelServiceEndpoint):
        checks (list[ModelServiceInstanceCheckItem]):
        router_deployment (ModelServiceInstanceCheckRouterDeployment | Unset):
        health (ModelServiceInstanceCheckHealth | Unset):
        ais (Any | Unset): AIS job view or error object.
        local_logs (ModelServiceInstanceLogs | Unset):
    """

    object_: ModelServiceInstanceCheckObject
    service_id: str
    instance_id: str
    checked_at: datetime.datetime
    overall_status: ModelServiceInstanceCheckOverallStatus
    service: ModelService
    instance: ModelServiceInstance
    endpoint: ModelServiceEndpoint
    checks: list[ModelServiceInstanceCheckItem]
    router_deployment: ModelServiceInstanceCheckRouterDeployment | Unset = UNSET
    health: ModelServiceInstanceCheckHealth | Unset = UNSET
    ais: Any | Unset = UNSET
    local_logs: ModelServiceInstanceLogs | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        object_ = self.object_.value

        service_id = self.service_id

        instance_id = self.instance_id

        checked_at = self.checked_at.isoformat()

        overall_status = self.overall_status.value

        service = self.service.to_dict()

        instance = self.instance.to_dict()

        endpoint = self.endpoint.to_dict()

        checks = []
        for checks_item_data in self.checks:
            checks_item = checks_item_data.to_dict()
            checks.append(checks_item)

        router_deployment: dict[str, Any] | Unset = UNSET
        if not isinstance(self.router_deployment, Unset):
            router_deployment = self.router_deployment.to_dict()

        health: dict[str, Any] | Unset = UNSET
        if not isinstance(self.health, Unset):
            health = self.health.to_dict()

        ais = self.ais

        local_logs: dict[str, Any] | Unset = UNSET
        if not isinstance(self.local_logs, Unset):
            local_logs = self.local_logs.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "object": object_,
                "service_id": service_id,
                "instance_id": instance_id,
                "checked_at": checked_at,
                "overall_status": overall_status,
                "service": service,
                "instance": instance,
                "endpoint": endpoint,
                "checks": checks,
            }
        )
        if router_deployment is not UNSET:
            field_dict["router_deployment"] = router_deployment
        if health is not UNSET:
            field_dict["health"] = health
        if ais is not UNSET:
            field_dict["ais"] = ais
        if local_logs is not UNSET:
            field_dict["local_logs"] = local_logs

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.model_service import ModelService
        from ..models.model_service_endpoint import ModelServiceEndpoint
        from ..models.model_service_instance import ModelServiceInstance
        from ..models.model_service_instance_check_health import (
            ModelServiceInstanceCheckHealth,
        )
        from ..models.model_service_instance_check_item import (
            ModelServiceInstanceCheckItem,
        )
        from ..models.model_service_instance_check_router_deployment import (
            ModelServiceInstanceCheckRouterDeployment,
        )
        from ..models.model_service_instance_logs import ModelServiceInstanceLogs

        d = dict(src_dict)
        object_ = ModelServiceInstanceCheckObject(d.pop("object"))

        service_id = d.pop("service_id")

        instance_id = d.pop("instance_id")

        checked_at = isoparse(d.pop("checked_at"))

        overall_status = ModelServiceInstanceCheckOverallStatus(d.pop("overall_status"))

        service = ModelService.from_dict(d.pop("service"))

        instance = ModelServiceInstance.from_dict(d.pop("instance"))

        endpoint = ModelServiceEndpoint.from_dict(d.pop("endpoint"))

        checks = []
        _checks = d.pop("checks")
        for checks_item_data in _checks:
            checks_item = ModelServiceInstanceCheckItem.from_dict(checks_item_data)

            checks.append(checks_item)

        _router_deployment = d.pop("router_deployment", UNSET)
        router_deployment: ModelServiceInstanceCheckRouterDeployment | Unset
        if isinstance(_router_deployment, Unset):
            router_deployment = UNSET
        else:
            router_deployment = ModelServiceInstanceCheckRouterDeployment.from_dict(
                _router_deployment
            )

        _health = d.pop("health", UNSET)
        health: ModelServiceInstanceCheckHealth | Unset
        if isinstance(_health, Unset):
            health = UNSET
        else:
            health = ModelServiceInstanceCheckHealth.from_dict(_health)

        ais = d.pop("ais", UNSET)

        _local_logs = d.pop("local_logs", UNSET)
        local_logs: ModelServiceInstanceLogs | Unset
        if isinstance(_local_logs, Unset):
            local_logs = UNSET
        else:
            local_logs = ModelServiceInstanceLogs.from_dict(_local_logs)

        model_service_instance_check = cls(
            object_=object_,
            service_id=service_id,
            instance_id=instance_id,
            checked_at=checked_at,
            overall_status=overall_status,
            service=service,
            instance=instance,
            endpoint=endpoint,
            checks=checks,
            router_deployment=router_deployment,
            health=health,
            ais=ais,
            local_logs=local_logs,
        )

        model_service_instance_check.additional_properties = d
        return model_service_instance_check

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
