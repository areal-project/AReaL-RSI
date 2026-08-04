from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="ModelServiceSglangSpecEnv")


@_attrs_define
class ModelServiceSglangSpecEnv:
    """Additional environment variables for SGLang. Variables managed by model-service are rejected, including
    MODEL_SERVICE_ID, MODEL_INSTANCE_ID, MODEL_PATH, SERVED_MODEL_NAME, CONTROL_BASE_URL, REGISTER_URL, STARTED_URL,
    REGISTER_TOKEN, SGLANG_PORT, SGLANG_PORT_INDEX, SGLANG_NNODES, SGLANG_NODE_RANK, SGLANG_DIST_INIT_ADDR, and
    SGLANG_LOG_DIR.

    """

    additional_properties: dict[str, str] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        model_service_sglang_spec_env = cls()

        model_service_sglang_spec_env.additional_properties = d
        return model_service_sglang_spec_env

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> str:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: str) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
