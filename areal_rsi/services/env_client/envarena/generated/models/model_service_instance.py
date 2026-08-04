from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.model_service_instance_status import ModelServiceInstanceStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_service_resource_spec import ModelServiceResourceSpec
    from ..models.model_service_sglang_spec import ModelServiceSglangSpec


T = TypeVar("T", bound="ModelServiceInstance")


@_attrs_define
class ModelServiceInstance:
    """
    Attributes:
        instance_id (str):
        service_id (str):
        model_path (str):
        resource_spec (ModelServiceResourceSpec):
        sglang (ModelServiceSglangSpec):
        node_num (int):
        status (ModelServiceInstanceStatus):
        created_at (datetime.datetime):
        updated_at (datetime.datetime):
        submit_attempts (int):
        image (str | Unset):
        job_name (str | Unset):
        ais_job_id (str | Unset):
        pod_ip (str | Unset):
        port (int | Unset):
        ready_at (datetime.datetime | None | Unset):
        last_health_at (datetime.datetime | None | Unset):
        stopped_at (datetime.datetime | None | Unset):
        error_message (str | Unset):
        bridge_last_error (str | Unset):
        runtime_seconds (float | Unset):
        created_by (str | Unset):
    """

    instance_id: str
    service_id: str
    model_path: str
    resource_spec: ModelServiceResourceSpec
    sglang: ModelServiceSglangSpec
    node_num: int
    status: ModelServiceInstanceStatus
    created_at: datetime.datetime
    updated_at: datetime.datetime
    submit_attempts: int
    image: str | Unset = UNSET
    job_name: str | Unset = UNSET
    ais_job_id: str | Unset = UNSET
    pod_ip: str | Unset = UNSET
    port: int | Unset = UNSET
    ready_at: datetime.datetime | None | Unset = UNSET
    last_health_at: datetime.datetime | None | Unset = UNSET
    stopped_at: datetime.datetime | None | Unset = UNSET
    error_message: str | Unset = UNSET
    bridge_last_error: str | Unset = UNSET
    runtime_seconds: float | Unset = UNSET
    created_by: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        instance_id = self.instance_id

        service_id = self.service_id

        model_path = self.model_path

        resource_spec = self.resource_spec.to_dict()

        sglang = self.sglang.to_dict()

        node_num = self.node_num

        status = self.status.value

        created_at = self.created_at.isoformat()

        updated_at = self.updated_at.isoformat()

        submit_attempts = self.submit_attempts

        image = self.image

        job_name = self.job_name

        ais_job_id = self.ais_job_id

        pod_ip = self.pod_ip

        port = self.port

        ready_at: None | str | Unset
        if isinstance(self.ready_at, Unset):
            ready_at = UNSET
        elif isinstance(self.ready_at, datetime.datetime):
            ready_at = self.ready_at.isoformat()
        else:
            ready_at = self.ready_at

        last_health_at: None | str | Unset
        if isinstance(self.last_health_at, Unset):
            last_health_at = UNSET
        elif isinstance(self.last_health_at, datetime.datetime):
            last_health_at = self.last_health_at.isoformat()
        else:
            last_health_at = self.last_health_at

        stopped_at: None | str | Unset
        if isinstance(self.stopped_at, Unset):
            stopped_at = UNSET
        elif isinstance(self.stopped_at, datetime.datetime):
            stopped_at = self.stopped_at.isoformat()
        else:
            stopped_at = self.stopped_at

        error_message = self.error_message

        bridge_last_error = self.bridge_last_error

        runtime_seconds = self.runtime_seconds

        created_by = self.created_by

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "instance_id": instance_id,
                "service_id": service_id,
                "model_path": model_path,
                "resource_spec": resource_spec,
                "sglang": sglang,
                "node_num": node_num,
                "status": status,
                "created_at": created_at,
                "updated_at": updated_at,
                "submit_attempts": submit_attempts,
            }
        )
        if image is not UNSET:
            field_dict["image"] = image
        if job_name is not UNSET:
            field_dict["job_name"] = job_name
        if ais_job_id is not UNSET:
            field_dict["ais_job_id"] = ais_job_id
        if pod_ip is not UNSET:
            field_dict["pod_ip"] = pod_ip
        if port is not UNSET:
            field_dict["port"] = port
        if ready_at is not UNSET:
            field_dict["ready_at"] = ready_at
        if last_health_at is not UNSET:
            field_dict["last_health_at"] = last_health_at
        if stopped_at is not UNSET:
            field_dict["stopped_at"] = stopped_at
        if error_message is not UNSET:
            field_dict["error_message"] = error_message
        if bridge_last_error is not UNSET:
            field_dict["bridge_last_error"] = bridge_last_error
        if runtime_seconds is not UNSET:
            field_dict["runtime_seconds"] = runtime_seconds
        if created_by is not UNSET:
            field_dict["created_by"] = created_by

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.model_service_resource_spec import ModelServiceResourceSpec
        from ..models.model_service_sglang_spec import ModelServiceSglangSpec

        d = dict(src_dict)
        instance_id = d.pop("instance_id")

        service_id = d.pop("service_id")

        model_path = d.pop("model_path")

        resource_spec = ModelServiceResourceSpec.from_dict(d.pop("resource_spec"))

        sglang = ModelServiceSglangSpec.from_dict(d.pop("sglang"))

        node_num = d.pop("node_num")

        status = ModelServiceInstanceStatus(d.pop("status"))

        created_at = isoparse(d.pop("created_at"))

        updated_at = isoparse(d.pop("updated_at"))

        submit_attempts = d.pop("submit_attempts")

        image = d.pop("image", UNSET)

        job_name = d.pop("job_name", UNSET)

        ais_job_id = d.pop("ais_job_id", UNSET)

        pod_ip = d.pop("pod_ip", UNSET)

        port = d.pop("port", UNSET)

        def _parse_ready_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                ready_at_type_0 = isoparse(data)

                return ready_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        ready_at = _parse_ready_at(d.pop("ready_at", UNSET))

        def _parse_last_health_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_health_at_type_0 = isoparse(data)

                return last_health_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        last_health_at = _parse_last_health_at(d.pop("last_health_at", UNSET))

        def _parse_stopped_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                stopped_at_type_0 = isoparse(data)

                return stopped_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        stopped_at = _parse_stopped_at(d.pop("stopped_at", UNSET))

        error_message = d.pop("error_message", UNSET)

        bridge_last_error = d.pop("bridge_last_error", UNSET)

        runtime_seconds = d.pop("runtime_seconds", UNSET)

        created_by = d.pop("created_by", UNSET)

        model_service_instance = cls(
            instance_id=instance_id,
            service_id=service_id,
            model_path=model_path,
            resource_spec=resource_spec,
            sglang=sglang,
            node_num=node_num,
            status=status,
            created_at=created_at,
            updated_at=updated_at,
            submit_attempts=submit_attempts,
            image=image,
            job_name=job_name,
            ais_job_id=ais_job_id,
            pod_ip=pod_ip,
            port=port,
            ready_at=ready_at,
            last_health_at=last_health_at,
            stopped_at=stopped_at,
            error_message=error_message,
            bridge_last_error=bridge_last_error,
            runtime_seconds=runtime_seconds,
            created_by=created_by,
        )

        model_service_instance.additional_properties = d
        return model_service_instance

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
