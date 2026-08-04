from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.import_model_service_migration_response_200_services_skipped_item import (
        ImportModelServiceMigrationResponse200ServicesSkippedItem,
    )


T = TypeVar("T", bound="ImportModelServiceMigrationResponse200")


@_attrs_define
class ImportModelServiceMigrationResponse200:
    """
    Attributes:
        catalog_models (int | Unset):
        catalog_images (int | Unset):
        services_imported (int | Unset):
        instances_imported (int | Unset):
        ready_gateway_endpoints (int | Unset):
        services_skipped (list[ImportModelServiceMigrationResponse200ServicesSkippedItem] | Unset):
    """

    catalog_models: int | Unset = UNSET
    catalog_images: int | Unset = UNSET
    services_imported: int | Unset = UNSET
    instances_imported: int | Unset = UNSET
    ready_gateway_endpoints: int | Unset = UNSET
    services_skipped: (
        list[ImportModelServiceMigrationResponse200ServicesSkippedItem] | Unset
    ) = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        catalog_models = self.catalog_models

        catalog_images = self.catalog_images

        services_imported = self.services_imported

        instances_imported = self.instances_imported

        ready_gateway_endpoints = self.ready_gateway_endpoints

        services_skipped: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.services_skipped, Unset):
            services_skipped = []
            for services_skipped_item_data in self.services_skipped:
                services_skipped_item = services_skipped_item_data.to_dict()
                services_skipped.append(services_skipped_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if catalog_models is not UNSET:
            field_dict["catalog_models"] = catalog_models
        if catalog_images is not UNSET:
            field_dict["catalog_images"] = catalog_images
        if services_imported is not UNSET:
            field_dict["services_imported"] = services_imported
        if instances_imported is not UNSET:
            field_dict["instances_imported"] = instances_imported
        if ready_gateway_endpoints is not UNSET:
            field_dict["ready_gateway_endpoints"] = ready_gateway_endpoints
        if services_skipped is not UNSET:
            field_dict["services_skipped"] = services_skipped

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.import_model_service_migration_response_200_services_skipped_item import (
            ImportModelServiceMigrationResponse200ServicesSkippedItem,
        )

        d = dict(src_dict)
        catalog_models = d.pop("catalog_models", UNSET)

        catalog_images = d.pop("catalog_images", UNSET)

        services_imported = d.pop("services_imported", UNSET)

        instances_imported = d.pop("instances_imported", UNSET)

        ready_gateway_endpoints = d.pop("ready_gateway_endpoints", UNSET)

        _services_skipped = d.pop("services_skipped", UNSET)
        services_skipped: (
            list[ImportModelServiceMigrationResponse200ServicesSkippedItem] | Unset
        ) = UNSET
        if _services_skipped is not UNSET:
            services_skipped = []
            for services_skipped_item_data in _services_skipped:
                services_skipped_item = (
                    ImportModelServiceMigrationResponse200ServicesSkippedItem.from_dict(
                        services_skipped_item_data
                    )
                )

                services_skipped.append(services_skipped_item)

        import_model_service_migration_response_200 = cls(
            catalog_models=catalog_models,
            catalog_images=catalog_images,
            services_imported=services_imported,
            instances_imported=instances_imported,
            ready_gateway_endpoints=ready_gateway_endpoints,
            services_skipped=services_skipped,
        )

        import_model_service_migration_response_200.additional_properties = d
        return import_model_service_migration_response_200

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
