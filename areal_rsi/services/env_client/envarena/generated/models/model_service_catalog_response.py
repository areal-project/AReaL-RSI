from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_service_catalog_image import ModelServiceCatalogImage
    from ..models.model_service_catalog_model import ModelServiceCatalogModel
    from ..models.model_service_catalog_response_image_usages import (
        ModelServiceCatalogResponseImageUsages,
    )


T = TypeVar("T", bound="ModelServiceCatalogResponse")


@_attrs_define
class ModelServiceCatalogResponse:
    """
    Attributes:
        models (list[ModelServiceCatalogModel]):
        images (list[ModelServiceCatalogImage]):
        image_usages (ModelServiceCatalogResponseImageUsages | Unset):
    """

    models: list[ModelServiceCatalogModel]
    images: list[ModelServiceCatalogImage]
    image_usages: ModelServiceCatalogResponseImageUsages | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        models = []
        for models_item_data in self.models:
            models_item = models_item_data.to_dict()
            models.append(models_item)

        images = []
        for images_item_data in self.images:
            images_item = images_item_data.to_dict()
            images.append(images_item)

        image_usages: dict[str, Any] | Unset = UNSET
        if not isinstance(self.image_usages, Unset):
            image_usages = self.image_usages.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "models": models,
                "images": images,
            }
        )
        if image_usages is not UNSET:
            field_dict["image_usages"] = image_usages

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.model_service_catalog_image import ModelServiceCatalogImage
        from ..models.model_service_catalog_model import ModelServiceCatalogModel
        from ..models.model_service_catalog_response_image_usages import (
            ModelServiceCatalogResponseImageUsages,
        )

        d = dict(src_dict)
        models = []
        _models = d.pop("models")
        for models_item_data in _models:
            models_item = ModelServiceCatalogModel.from_dict(models_item_data)

            models.append(models_item)

        images = []
        _images = d.pop("images")
        for images_item_data in _images:
            images_item = ModelServiceCatalogImage.from_dict(images_item_data)

            images.append(images_item)

        _image_usages = d.pop("image_usages", UNSET)
        image_usages: ModelServiceCatalogResponseImageUsages | Unset
        if isinstance(_image_usages, Unset):
            image_usages = UNSET
        else:
            image_usages = ModelServiceCatalogResponseImageUsages.from_dict(
                _image_usages
            )

        model_service_catalog_response = cls(
            models=models,
            images=images,
            image_usages=image_usages,
        )

        model_service_catalog_response.additional_properties = d
        return model_service_catalog_response

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
