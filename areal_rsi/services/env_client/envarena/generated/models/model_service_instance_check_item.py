from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.model_service_instance_check_item_status import (
    ModelServiceInstanceCheckItemStatus,
)
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_service_instance_check_item_details import (
        ModelServiceInstanceCheckItemDetails,
    )


T = TypeVar("T", bound="ModelServiceInstanceCheckItem")


@_attrs_define
class ModelServiceInstanceCheckItem:
    """
    Attributes:
        key (str):
        label (str):
        status (ModelServiceInstanceCheckItemStatus):
        message (str):
        details (ModelServiceInstanceCheckItemDetails | Unset):
    """

    key: str
    label: str
    status: ModelServiceInstanceCheckItemStatus
    message: str
    details: ModelServiceInstanceCheckItemDetails | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        key = self.key

        label = self.label

        status = self.status.value

        message = self.message

        details: dict[str, Any] | Unset = UNSET
        if not isinstance(self.details, Unset):
            details = self.details.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "key": key,
                "label": label,
                "status": status,
                "message": message,
            }
        )
        if details is not UNSET:
            field_dict["details"] = details

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.model_service_instance_check_item_details import (
            ModelServiceInstanceCheckItemDetails,
        )

        d = dict(src_dict)
        key = d.pop("key")

        label = d.pop("label")

        status = ModelServiceInstanceCheckItemStatus(d.pop("status"))

        message = d.pop("message")

        _details = d.pop("details", UNSET)
        details: ModelServiceInstanceCheckItemDetails | Unset
        if isinstance(_details, Unset):
            details = UNSET
        else:
            details = ModelServiceInstanceCheckItemDetails.from_dict(_details)

        model_service_instance_check_item = cls(
            key=key,
            label=label,
            status=status,
            message=message,
            details=details,
        )

        model_service_instance_check_item.additional_properties = d
        return model_service_instance_check_item

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
