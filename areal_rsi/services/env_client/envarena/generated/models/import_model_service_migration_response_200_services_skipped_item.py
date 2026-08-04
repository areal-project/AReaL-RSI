from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ImportModelServiceMigrationResponse200ServicesSkippedItem")


@_attrs_define
class ImportModelServiceMigrationResponse200ServicesSkippedItem:
    """
    Attributes:
        service_id (str | Unset):
        served_model_name (str | Unset):
        reason (str | Unset):
    """

    service_id: str | Unset = UNSET
    served_model_name: str | Unset = UNSET
    reason: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        service_id = self.service_id

        served_model_name = self.served_model_name

        reason = self.reason

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if service_id is not UNSET:
            field_dict["service_id"] = service_id
        if served_model_name is not UNSET:
            field_dict["served_model_name"] = served_model_name
        if reason is not UNSET:
            field_dict["reason"] = reason

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        service_id = d.pop("service_id", UNSET)

        served_model_name = d.pop("served_model_name", UNSET)

        reason = d.pop("reason", UNSET)

        import_model_service_migration_response_200_services_skipped_item = cls(
            service_id=service_id,
            served_model_name=served_model_name,
            reason=reason,
        )

        import_model_service_migration_response_200_services_skipped_item.additional_properties = d
        return import_model_service_migration_response_200_services_skipped_item

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
