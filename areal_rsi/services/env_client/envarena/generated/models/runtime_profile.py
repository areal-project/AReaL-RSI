from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.runtime_profile_network import RuntimeProfileNetwork
from ..types import UNSET, Unset

T = TypeVar("T", bound="RuntimeProfile")


@_attrs_define
class RuntimeProfile:
    """
    Attributes:
        key (str):  Example: image_networked.
        label (str):  Example: 有网络.
        kind (str):  Example: image.
        network (RuntimeProfileNetwork | Unset):
    """

    key: str
    label: str
    kind: str
    network: RuntimeProfileNetwork | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        key = self.key

        label = self.label

        kind = self.kind

        network: str | Unset = UNSET
        if not isinstance(self.network, Unset):
            network = self.network.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "key": key,
                "label": label,
                "kind": kind,
            }
        )
        if network is not UNSET:
            field_dict["network"] = network

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        key = d.pop("key")

        label = d.pop("label")

        kind = d.pop("kind")

        _network = d.pop("network", UNSET)
        network: RuntimeProfileNetwork | Unset
        if isinstance(_network, Unset):
            network = UNSET
        else:
            network = RuntimeProfileNetwork(_network)

        runtime_profile = cls(
            key=key,
            label=label,
            kind=kind,
            network=network,
        )

        runtime_profile.additional_properties = d
        return runtime_profile

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
