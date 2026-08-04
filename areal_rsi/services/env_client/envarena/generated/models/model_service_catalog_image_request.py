from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="ModelServiceCatalogImageRequest")


@_attrs_define
class ModelServiceCatalogImageRequest:
    """
    Attributes:
        image (str):
    """

    image: str

    def to_dict(self) -> dict[str, Any]:
        image = self.image

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "image": image,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        image = d.pop("image")

        model_service_catalog_image_request = cls(
            image=image,
        )

        return model_service_catalog_image_request
