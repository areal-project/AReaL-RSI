from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.llm_model_endpoint_request import LLMModelEndpointRequest
    from ..models.update_llm_model_request_metadata import UpdateLLMModelRequestMetadata


T = TypeVar("T", bound="UpdateLLMModelRequest")


@_attrs_define
class UpdateLLMModelRequest:
    """
    Attributes:
        endpoints (list[LLMModelEndpointRequest] | Unset):
        enabled (bool | Unset):
        metadata (UpdateLLMModelRequestMetadata | Unset):
    """

    endpoints: list[LLMModelEndpointRequest] | Unset = UNSET
    enabled: bool | Unset = UNSET
    metadata: UpdateLLMModelRequestMetadata | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        endpoints: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.endpoints, Unset):
            endpoints = []
            for endpoints_item_data in self.endpoints:
                endpoints_item = endpoints_item_data.to_dict()
                endpoints.append(endpoints_item)

        enabled = self.enabled

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if endpoints is not UNSET:
            field_dict["endpoints"] = endpoints
        if enabled is not UNSET:
            field_dict["enabled"] = enabled
        if metadata is not UNSET:
            field_dict["metadata"] = metadata

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.llm_model_endpoint_request import LLMModelEndpointRequest
        from ..models.update_llm_model_request_metadata import (
            UpdateLLMModelRequestMetadata,
        )

        d = dict(src_dict)
        _endpoints = d.pop("endpoints", UNSET)
        endpoints: list[LLMModelEndpointRequest] | Unset = UNSET
        if _endpoints is not UNSET:
            endpoints = []
            for endpoints_item_data in _endpoints:
                endpoints_item = LLMModelEndpointRequest.from_dict(endpoints_item_data)

                endpoints.append(endpoints_item)

        enabled = d.pop("enabled", UNSET)

        _metadata = d.pop("metadata", UNSET)
        metadata: UpdateLLMModelRequestMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = UpdateLLMModelRequestMetadata.from_dict(_metadata)

        update_llm_model_request = cls(
            endpoints=endpoints,
            enabled=enabled,
            metadata=metadata,
        )

        update_llm_model_request.additional_properties = d
        return update_llm_model_request

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
