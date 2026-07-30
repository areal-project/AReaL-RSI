from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.runtime_config_runtime_profiles import RuntimeConfigRuntimeProfiles


T = TypeVar("T", bound="RuntimeConfig")


@_attrs_define
class RuntimeConfig:
    """
    Attributes:
        host_addr (str): Public EnvArena service origin, usually provided by HOST_ADDR. Example:
            http://envarena.areal.svc.tydd-staging.alipay.net.
        openapi_base_url (str): Absolute OpenAPI v1 base URL rendered into copied Registry Skills. Points at the API
            port. Example: http://envarena.areal.svc.tydd-staging.alipay.net:8080/openapi/v1.
        runtime_profiles (RuntimeConfigRuntimeProfiles | Unset): Public runtime profile choices. Template IDs are
            intentionally omitted.
    """

    host_addr: str
    openapi_base_url: str
    runtime_profiles: RuntimeConfigRuntimeProfiles | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        host_addr = self.host_addr

        openapi_base_url = self.openapi_base_url

        runtime_profiles: dict[str, Any] | Unset = UNSET
        if not isinstance(self.runtime_profiles, Unset):
            runtime_profiles = self.runtime_profiles.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "host_addr": host_addr,
                "openapi_base_url": openapi_base_url,
            }
        )
        if runtime_profiles is not UNSET:
            field_dict["runtime_profiles"] = runtime_profiles

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.runtime_config_runtime_profiles import (
            RuntimeConfigRuntimeProfiles,
        )

        d = dict(src_dict)
        host_addr = d.pop("host_addr")

        openapi_base_url = d.pop("openapi_base_url")

        _runtime_profiles = d.pop("runtime_profiles", UNSET)
        runtime_profiles: RuntimeConfigRuntimeProfiles | Unset
        if isinstance(_runtime_profiles, Unset):
            runtime_profiles = UNSET
        else:
            runtime_profiles = RuntimeConfigRuntimeProfiles.from_dict(_runtime_profiles)

        runtime_config = cls(
            host_addr=host_addr,
            openapi_base_url=openapi_base_url,
            runtime_profiles=runtime_profiles,
        )

        runtime_config.additional_properties = d
        return runtime_config

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
