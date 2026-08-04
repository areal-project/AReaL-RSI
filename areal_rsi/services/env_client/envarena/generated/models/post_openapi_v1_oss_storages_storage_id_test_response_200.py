from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="PostOpenapiV1OssStoragesStorageIdTestResponse200")


@_attrs_define
class PostOpenapiV1OssStoragesStorageIdTestResponse200:
    """
    Attributes:
        ok (bool):
        storage_id (str):
        latency_ms (int):
    """

    ok: bool
    storage_id: str
    latency_ms: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        ok = self.ok

        storage_id = self.storage_id

        latency_ms = self.latency_ms

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "ok": ok,
                "storage_id": storage_id,
                "latency_ms": latency_ms,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        ok = d.pop("ok")

        storage_id = d.pop("storage_id")

        latency_ms = d.pop("latency_ms")

        post_openapi_v1_oss_storages_storage_id_test_response_200 = cls(
            ok=ok,
            storage_id=storage_id,
            latency_ms=latency_ms,
        )

        post_openapi_v1_oss_storages_storage_id_test_response_200.additional_properties = d
        return post_openapi_v1_oss_storages_storage_id_test_response_200

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
