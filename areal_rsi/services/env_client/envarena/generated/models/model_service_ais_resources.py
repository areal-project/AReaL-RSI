from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_service_ais_resources_jobs_item import (
        ModelServiceAISResourcesJobsItem,
    )
    from ..models.model_service_ais_resources_status_counts import (
        ModelServiceAISResourcesStatusCounts,
    )


T = TypeVar("T", bound="ModelServiceAISResources")


@_attrs_define
class ModelServiceAISResources:
    """
    Attributes:
        available (bool | Unset):
        source (str | Unset):
        limits_available (bool | Unset):
        jobs_count (int | Unset):
        status_counts (ModelServiceAISResourcesStatusCounts | Unset):
        jobs (list[ModelServiceAISResourcesJobsItem] | Unset):
    """

    available: bool | Unset = UNSET
    source: str | Unset = UNSET
    limits_available: bool | Unset = UNSET
    jobs_count: int | Unset = UNSET
    status_counts: ModelServiceAISResourcesStatusCounts | Unset = UNSET
    jobs: list[ModelServiceAISResourcesJobsItem] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        available = self.available

        source = self.source

        limits_available = self.limits_available

        jobs_count = self.jobs_count

        status_counts: dict[str, Any] | Unset = UNSET
        if not isinstance(self.status_counts, Unset):
            status_counts = self.status_counts.to_dict()

        jobs: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.jobs, Unset):
            jobs = []
            for jobs_item_data in self.jobs:
                jobs_item = jobs_item_data.to_dict()
                jobs.append(jobs_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if available is not UNSET:
            field_dict["available"] = available
        if source is not UNSET:
            field_dict["source"] = source
        if limits_available is not UNSET:
            field_dict["limits_available"] = limits_available
        if jobs_count is not UNSET:
            field_dict["jobs_count"] = jobs_count
        if status_counts is not UNSET:
            field_dict["status_counts"] = status_counts
        if jobs is not UNSET:
            field_dict["jobs"] = jobs

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.model_service_ais_resources_jobs_item import (
            ModelServiceAISResourcesJobsItem,
        )
        from ..models.model_service_ais_resources_status_counts import (
            ModelServiceAISResourcesStatusCounts,
        )

        d = dict(src_dict)
        available = d.pop("available", UNSET)

        source = d.pop("source", UNSET)

        limits_available = d.pop("limits_available", UNSET)

        jobs_count = d.pop("jobs_count", UNSET)

        _status_counts = d.pop("status_counts", UNSET)
        status_counts: ModelServiceAISResourcesStatusCounts | Unset
        if isinstance(_status_counts, Unset):
            status_counts = UNSET
        else:
            status_counts = ModelServiceAISResourcesStatusCounts.from_dict(
                _status_counts
            )

        _jobs = d.pop("jobs", UNSET)
        jobs: list[ModelServiceAISResourcesJobsItem] | Unset = UNSET
        if _jobs is not UNSET:
            jobs = []
            for jobs_item_data in _jobs:
                jobs_item = ModelServiceAISResourcesJobsItem.from_dict(jobs_item_data)

                jobs.append(jobs_item)

        model_service_ais_resources = cls(
            available=available,
            source=source,
            limits_available=limits_available,
            jobs_count=jobs_count,
            status_counts=status_counts,
            jobs=jobs,
        )

        model_service_ais_resources.additional_properties = d
        return model_service_ais_resources

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
