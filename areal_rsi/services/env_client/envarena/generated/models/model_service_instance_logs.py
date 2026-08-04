from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_service_instance_log_file import ModelServiceInstanceLogFile


T = TypeVar("T", bound="ModelServiceInstanceLogs")


@_attrs_define
class ModelServiceInstanceLogs:
    """
    Attributes:
        service_id (str):
        instance_id (str):
        source (str):
        available (bool):
        files_count (int):
        files (list[ModelServiceInstanceLogFile]):
        log_dir (str | Unset):
        message (str | Unset):
    """

    service_id: str
    instance_id: str
    source: str
    available: bool
    files_count: int
    files: list[ModelServiceInstanceLogFile]
    log_dir: str | Unset = UNSET
    message: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        service_id = self.service_id

        instance_id = self.instance_id

        source = self.source

        available = self.available

        files_count = self.files_count

        files = []
        for files_item_data in self.files:
            files_item = files_item_data.to_dict()
            files.append(files_item)

        log_dir = self.log_dir

        message = self.message

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "service_id": service_id,
                "instance_id": instance_id,
                "source": source,
                "available": available,
                "files_count": files_count,
                "files": files,
            }
        )
        if log_dir is not UNSET:
            field_dict["log_dir"] = log_dir
        if message is not UNSET:
            field_dict["message"] = message

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.model_service_instance_log_file import ModelServiceInstanceLogFile

        d = dict(src_dict)
        service_id = d.pop("service_id")

        instance_id = d.pop("instance_id")

        source = d.pop("source")

        available = d.pop("available")

        files_count = d.pop("files_count")

        files = []
        _files = d.pop("files")
        for files_item_data in _files:
            files_item = ModelServiceInstanceLogFile.from_dict(files_item_data)

            files.append(files_item)

        log_dir = d.pop("log_dir", UNSET)

        message = d.pop("message", UNSET)

        model_service_instance_logs = cls(
            service_id=service_id,
            instance_id=instance_id,
            source=source,
            available=available,
            files_count=files_count,
            files=files,
            log_dir=log_dir,
            message=message,
        )

        model_service_instance_logs.additional_properties = d
        return model_service_instance_logs

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
