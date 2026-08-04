from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.model_service_catalog_parameters_response_object import (
    ModelServiceCatalogParametersResponseObject,
)

if TYPE_CHECKING:
    from ..models.model_service_catalog_parameters import ModelServiceCatalogParameters


T = TypeVar("T", bound="ModelServiceCatalogParametersResponse")


@_attrs_define
class ModelServiceCatalogParametersResponse:
    """
    Attributes:
        object_ (ModelServiceCatalogParametersResponseObject):
        count (int):
        data (list[ModelServiceCatalogParameters]):
    """

    object_: ModelServiceCatalogParametersResponseObject
    count: int
    data: list[ModelServiceCatalogParameters]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        object_ = self.object_.value

        count = self.count

        data = []
        for data_item_data in self.data:
            data_item = data_item_data.to_dict()
            data.append(data_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "object": object_,
                "count": count,
                "data": data,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.model_service_catalog_parameters import (
            ModelServiceCatalogParameters,
        )

        d = dict(src_dict)
        object_ = ModelServiceCatalogParametersResponseObject(d.pop("object"))

        count = d.pop("count")

        data = []
        _data = d.pop("data")
        for data_item_data in _data:
            data_item = ModelServiceCatalogParameters.from_dict(data_item_data)

            data.append(data_item)

        model_service_catalog_parameters_response = cls(
            object_=object_,
            count=count,
            data=data,
        )

        model_service_catalog_parameters_response.additional_properties = d
        return model_service_catalog_parameters_response

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
