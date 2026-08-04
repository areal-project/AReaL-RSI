from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="ModelServiceDataStoreConfig")


@_attrs_define
class ModelServiceDataStoreConfig:
    """
    Attributes:
        mount_point (str):
        store_name (str):
        sub_path (str):
    """

    mount_point: str
    store_name: str
    sub_path: str

    def to_dict(self) -> dict[str, Any]:
        mount_point = self.mount_point

        store_name = self.store_name

        sub_path = self.sub_path

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "mount_point": mount_point,
                "store_name": store_name,
                "sub_path": sub_path,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        mount_point = d.pop("mount_point")

        store_name = d.pop("store_name")

        sub_path = d.pop("sub_path")

        model_service_data_store_config = cls(
            mount_point=mount_point,
            store_name=store_name,
            sub_path=sub_path,
        )

        return model_service_data_store_config
