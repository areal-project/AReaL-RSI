from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.object_ref import ObjectRef


T = TypeVar("T", bound="HarnessMaterialsSpec")


@_attrs_define
class HarnessMaterialsSpec:
    """
    Attributes:
        mcp_config (ObjectRef | Unset):
        settings (ObjectRef | Unset):
        agents (ObjectRef | Unset):
        commands (ObjectRef | Unset):
    """

    mcp_config: ObjectRef | Unset = UNSET
    settings: ObjectRef | Unset = UNSET
    agents: ObjectRef | Unset = UNSET
    commands: ObjectRef | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        mcp_config: dict[str, Any] | Unset = UNSET
        if not isinstance(self.mcp_config, Unset):
            mcp_config = self.mcp_config.to_dict()

        settings: dict[str, Any] | Unset = UNSET
        if not isinstance(self.settings, Unset):
            settings = self.settings.to_dict()

        agents: dict[str, Any] | Unset = UNSET
        if not isinstance(self.agents, Unset):
            agents = self.agents.to_dict()

        commands: dict[str, Any] | Unset = UNSET
        if not isinstance(self.commands, Unset):
            commands = self.commands.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if mcp_config is not UNSET:
            field_dict["mcp_config"] = mcp_config
        if settings is not UNSET:
            field_dict["settings"] = settings
        if agents is not UNSET:
            field_dict["agents"] = agents
        if commands is not UNSET:
            field_dict["commands"] = commands

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.object_ref import ObjectRef

        d = dict(src_dict)
        _mcp_config = d.pop("mcp_config", UNSET)
        mcp_config: ObjectRef | Unset
        if isinstance(_mcp_config, Unset):
            mcp_config = UNSET
        else:
            mcp_config = ObjectRef.from_dict(_mcp_config)

        _settings = d.pop("settings", UNSET)
        settings: ObjectRef | Unset
        if isinstance(_settings, Unset):
            settings = UNSET
        else:
            settings = ObjectRef.from_dict(_settings)

        _agents = d.pop("agents", UNSET)
        agents: ObjectRef | Unset
        if isinstance(_agents, Unset):
            agents = UNSET
        else:
            agents = ObjectRef.from_dict(_agents)

        _commands = d.pop("commands", UNSET)
        commands: ObjectRef | Unset
        if isinstance(_commands, Unset):
            commands = UNSET
        else:
            commands = ObjectRef.from_dict(_commands)

        harness_materials_spec = cls(
            mcp_config=mcp_config,
            settings=settings,
            agents=agents,
            commands=commands,
        )

        harness_materials_spec.additional_properties = d
        return harness_materials_spec

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
