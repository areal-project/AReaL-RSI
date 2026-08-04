from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

T = TypeVar("T", bound="ModelServiceCatalogImage")


@_attrs_define
class ModelServiceCatalogImage:
    """
    Attributes:
        image (str):
        created_at (datetime.datetime):
        updated_at (datetime.datetime):
        source (str | Unset):
        created_by (str | Unset):
    """

    image: str
    created_at: datetime.datetime
    updated_at: datetime.datetime
    source: str | Unset = UNSET
    created_by: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        image = self.image

        created_at = self.created_at.isoformat()

        updated_at = self.updated_at.isoformat()

        source = self.source

        created_by = self.created_by

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "image": image,
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )
        if source is not UNSET:
            field_dict["source"] = source
        if created_by is not UNSET:
            field_dict["created_by"] = created_by

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        image = d.pop("image")

        created_at = isoparse(d.pop("created_at"))

        updated_at = isoparse(d.pop("updated_at"))

        source = d.pop("source", UNSET)

        created_by = d.pop("created_by", UNSET)

        model_service_catalog_image = cls(
            image=image,
            created_at=created_at,
            updated_at=updated_at,
            source=source,
            created_by=created_by,
        )

        model_service_catalog_image.additional_properties = d
        return model_service_catalog_image

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
