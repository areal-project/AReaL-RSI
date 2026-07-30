from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.llm_model_endpoint import LLMModelEndpoint
    from ..models.llm_model_metadata import LLMModelMetadata


T = TypeVar("T", bound="LLMModel")


@_attrs_define
class LLMModel:
    """
    Attributes:
        model_name (str): Client-facing model alias used in /v1 requests.
        endpoints (list[LLMModelEndpoint]): Independently weighted upstream endpoints for this model alias.
        enabled (bool): Enables or disables the whole model alias. Default: True.
        metadata (LLMModelMetadata | Unset):
        created_at (datetime.datetime | Unset):
        updated_at (datetime.datetime | Unset):
        created_by (str | Unset):
    """

    model_name: str
    endpoints: list[LLMModelEndpoint]
    enabled: bool = True
    metadata: LLMModelMetadata | Unset = UNSET
    created_at: datetime.datetime | Unset = UNSET
    updated_at: datetime.datetime | Unset = UNSET
    created_by: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        model_name = self.model_name

        endpoints = []
        for endpoints_item_data in self.endpoints:
            endpoints_item = endpoints_item_data.to_dict()
            endpoints.append(endpoints_item)

        enabled = self.enabled

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

        created_at: str | Unset = UNSET
        if not isinstance(self.created_at, Unset):
            created_at = self.created_at.isoformat()

        updated_at: str | Unset = UNSET
        if not isinstance(self.updated_at, Unset):
            updated_at = self.updated_at.isoformat()

        created_by = self.created_by

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "model_name": model_name,
                "endpoints": endpoints,
                "enabled": enabled,
            }
        )
        if metadata is not UNSET:
            field_dict["metadata"] = metadata
        if created_at is not UNSET:
            field_dict["created_at"] = created_at
        if updated_at is not UNSET:
            field_dict["updated_at"] = updated_at
        if created_by is not UNSET:
            field_dict["created_by"] = created_by

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.llm_model_endpoint import LLMModelEndpoint
        from ..models.llm_model_metadata import LLMModelMetadata

        d = dict(src_dict)
        model_name = d.pop("model_name")

        endpoints = []
        _endpoints = d.pop("endpoints")
        for endpoints_item_data in _endpoints:
            endpoints_item = LLMModelEndpoint.from_dict(endpoints_item_data)

            endpoints.append(endpoints_item)

        enabled = d.pop("enabled")

        _metadata = d.pop("metadata", UNSET)
        metadata: LLMModelMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = LLMModelMetadata.from_dict(_metadata)

        _created_at = d.pop("created_at", UNSET)
        created_at: datetime.datetime | Unset
        if isinstance(_created_at, Unset):
            created_at = UNSET
        else:
            created_at = isoparse(_created_at)

        _updated_at = d.pop("updated_at", UNSET)
        updated_at: datetime.datetime | Unset
        if isinstance(_updated_at, Unset):
            updated_at = UNSET
        else:
            updated_at = isoparse(_updated_at)

        created_by = d.pop("created_by", UNSET)

        llm_model = cls(
            model_name=model_name,
            endpoints=endpoints,
            enabled=enabled,
            metadata=metadata,
            created_at=created_at,
            updated_at=updated_at,
            created_by=created_by,
        )

        llm_model.additional_properties = d
        return llm_model

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
