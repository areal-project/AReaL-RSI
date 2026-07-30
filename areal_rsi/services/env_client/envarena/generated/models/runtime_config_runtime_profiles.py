from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.runtime_profile import RuntimeProfile


T = TypeVar("T", bound="RuntimeConfigRuntimeProfiles")


@_attrs_define
class RuntimeConfigRuntimeProfiles:
    """Public runtime profile choices. Template IDs are intentionally omitted.

    Attributes:
        env (list[RuntimeProfile] | Unset):
        agent (list[RuntimeProfile] | Unset):
    """

    env: list[RuntimeProfile] | Unset = UNSET
    agent: list[RuntimeProfile] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        env: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.env, Unset):
            env = []
            for env_item_data in self.env:
                env_item = env_item_data.to_dict()
                env.append(env_item)

        agent: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.agent, Unset):
            agent = []
            for agent_item_data in self.agent:
                agent_item = agent_item_data.to_dict()
                agent.append(agent_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if env is not UNSET:
            field_dict["env"] = env
        if agent is not UNSET:
            field_dict["agent"] = agent

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.runtime_profile import RuntimeProfile

        d = dict(src_dict)
        _env = d.pop("env", UNSET)
        env: list[RuntimeProfile] | Unset = UNSET
        if _env is not UNSET:
            env = []
            for env_item_data in _env:
                env_item = RuntimeProfile.from_dict(env_item_data)

                env.append(env_item)

        _agent = d.pop("agent", UNSET)
        agent: list[RuntimeProfile] | Unset = UNSET
        if _agent is not UNSET:
            agent = []
            for agent_item_data in _agent:
                agent_item = RuntimeProfile.from_dict(agent_item_data)

                agent.append(agent_item)

        runtime_config_runtime_profiles = cls(
            env=env,
            agent=agent,
        )

        runtime_config_runtime_profiles.additional_properties = d
        return runtime_config_runtime_profiles

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
