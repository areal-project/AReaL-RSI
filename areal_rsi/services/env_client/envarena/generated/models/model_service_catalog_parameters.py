from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.model_service_catalog_parameters_object import (
    ModelServiceCatalogParametersObject,
)
from ..types import UNSET, Unset

T = TypeVar("T", bound="ModelServiceCatalogParameters")


@_attrs_define
class ModelServiceCatalogParameters:
    """
    Attributes:
        object_ (ModelServiceCatalogParametersObject):
        model_id (str):
        model_name (str):
        model_path (str):
        exists (bool):
        readable (bool):
        family (str | Unset):
        parameter_count (int | Unset):
        parameter_size (str | Unset):
    """

    object_: ModelServiceCatalogParametersObject
    model_id: str
    model_name: str
    model_path: str
    exists: bool
    readable: bool
    family: str | Unset = UNSET
    parameter_count: int | Unset = UNSET
    parameter_size: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        object_ = self.object_.value

        model_id = self.model_id

        model_name = self.model_name

        model_path = self.model_path

        exists = self.exists

        readable = self.readable

        family = self.family

        parameter_count = self.parameter_count

        parameter_size = self.parameter_size

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "object": object_,
                "model_id": model_id,
                "model_name": model_name,
                "model_path": model_path,
                "exists": exists,
                "readable": readable,
            }
        )
        if family is not UNSET:
            field_dict["family"] = family
        if parameter_count is not UNSET:
            field_dict["parameter_count"] = parameter_count
        if parameter_size is not UNSET:
            field_dict["parameter_size"] = parameter_size

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        object_ = ModelServiceCatalogParametersObject(d.pop("object"))

        model_id = d.pop("model_id")

        model_name = d.pop("model_name")

        model_path = d.pop("model_path")

        exists = d.pop("exists")

        readable = d.pop("readable")

        family = d.pop("family", UNSET)

        parameter_count = d.pop("parameter_count", UNSET)

        parameter_size = d.pop("parameter_size", UNSET)

        model_service_catalog_parameters = cls(
            object_=object_,
            model_id=model_id,
            model_name=model_name,
            model_path=model_path,
            exists=exists,
            readable=readable,
            family=family,
            parameter_count=parameter_count,
            parameter_size=parameter_size,
        )

        model_service_catalog_parameters.additional_properties = d
        return model_service_catalog_parameters

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
