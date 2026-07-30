from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.harness_agent_spec_delivery import HarnessAgentSpecDelivery
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.harness_agent_spec_env import HarnessAgentSpecEnv
    from ..models.harness_agent_spec_metadata import HarnessAgentSpecMetadata


T = TypeVar("T", bound="HarnessAgentSpec")


@_attrs_define
class HarnessAgentSpec:
    """
    Attributes:
        delivery (HarnessAgentSpecDelivery | Unset):
        env (HarnessAgentSpecEnv | Unset):
        metadata (HarnessAgentSpecMetadata | Unset):
    """

    delivery: HarnessAgentSpecDelivery | Unset = UNSET
    env: HarnessAgentSpecEnv | Unset = UNSET
    metadata: HarnessAgentSpecMetadata | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        delivery: str | Unset = UNSET
        if not isinstance(self.delivery, Unset):
            delivery = self.delivery.value

        env: dict[str, Any] | Unset = UNSET
        if not isinstance(self.env, Unset):
            env = self.env.to_dict()

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if delivery is not UNSET:
            field_dict["delivery"] = delivery
        if env is not UNSET:
            field_dict["env"] = env
        if metadata is not UNSET:
            field_dict["metadata"] = metadata

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.harness_agent_spec_env import HarnessAgentSpecEnv
        from ..models.harness_agent_spec_metadata import HarnessAgentSpecMetadata

        d = dict(src_dict)
        _delivery = d.pop("delivery", UNSET)
        delivery: HarnessAgentSpecDelivery | Unset
        if isinstance(_delivery, Unset):
            delivery = UNSET
        else:
            delivery = HarnessAgentSpecDelivery(_delivery)

        _env = d.pop("env", UNSET)
        env: HarnessAgentSpecEnv | Unset
        if isinstance(_env, Unset):
            env = UNSET
        else:
            env = HarnessAgentSpecEnv.from_dict(_env)

        _metadata = d.pop("metadata", UNSET)
        metadata: HarnessAgentSpecMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = HarnessAgentSpecMetadata.from_dict(_metadata)

        harness_agent_spec = cls(
            delivery=delivery,
            env=env,
            metadata=metadata,
        )

        harness_agent_spec.additional_properties = d
        return harness_agent_spec

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
