from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.harness_agent_spec import HarnessAgentSpec
    from ..models.harness_materials_spec import HarnessMaterialsSpec
    from ..models.harness_runtime_spec import HarnessRuntimeSpec
    from ..models.harness_spec_metadata import HarnessSpecMetadata
    from ..models.io_contract import IOContract
    from ..models.resource_spec import ResourceSpec
    from ..models.system_prompt_spec import SystemPromptSpec
    from ..models.version_ref import VersionRef


T = TypeVar("T", bound="HarnessSpec")


@_attrs_define
class HarnessSpec:
    """Harness is system_prompt + tool_refs + skill_refs plus a versioned agent runtime binary. The platform still chooses
    the execution substrate from the bound Env runtime.

        Attributes:
            runtime (HarnessRuntimeSpec):
            ttl_seconds (int):
            agent (HarnessAgentSpec | Unset):
            materials (HarnessMaterialsSpec | Unset):
            system_prompt (SystemPromptSpec | Unset):
            tool_refs (list[VersionRef] | Unset):
            skill_refs (list[VersionRef] | Unset):
            io_contract (IOContract | Unset):
            resource_spec (ResourceSpec | Unset):
            capabilities (list[str] | Unset):
            metadata (HarnessSpecMetadata | Unset):
    """

    runtime: HarnessRuntimeSpec
    ttl_seconds: int
    agent: HarnessAgentSpec | Unset = UNSET
    materials: HarnessMaterialsSpec | Unset = UNSET
    system_prompt: SystemPromptSpec | Unset = UNSET
    tool_refs: list[VersionRef] | Unset = UNSET
    skill_refs: list[VersionRef] | Unset = UNSET
    io_contract: IOContract | Unset = UNSET
    resource_spec: ResourceSpec | Unset = UNSET
    capabilities: list[str] | Unset = UNSET
    metadata: HarnessSpecMetadata | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        runtime = self.runtime.to_dict()

        ttl_seconds = self.ttl_seconds

        agent: dict[str, Any] | Unset = UNSET
        if not isinstance(self.agent, Unset):
            agent = self.agent.to_dict()

        materials: dict[str, Any] | Unset = UNSET
        if not isinstance(self.materials, Unset):
            materials = self.materials.to_dict()

        system_prompt: dict[str, Any] | Unset = UNSET
        if not isinstance(self.system_prompt, Unset):
            system_prompt = self.system_prompt.to_dict()

        tool_refs: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.tool_refs, Unset):
            tool_refs = []
            for tool_refs_item_data in self.tool_refs:
                tool_refs_item = tool_refs_item_data.to_dict()
                tool_refs.append(tool_refs_item)

        skill_refs: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.skill_refs, Unset):
            skill_refs = []
            for skill_refs_item_data in self.skill_refs:
                skill_refs_item = skill_refs_item_data.to_dict()
                skill_refs.append(skill_refs_item)

        io_contract: dict[str, Any] | Unset = UNSET
        if not isinstance(self.io_contract, Unset):
            io_contract = self.io_contract.to_dict()

        resource_spec: dict[str, Any] | Unset = UNSET
        if not isinstance(self.resource_spec, Unset):
            resource_spec = self.resource_spec.to_dict()

        capabilities: list[str] | Unset = UNSET
        if not isinstance(self.capabilities, Unset):
            capabilities = self.capabilities

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "runtime": runtime,
                "ttl_seconds": ttl_seconds,
            }
        )
        if agent is not UNSET:
            field_dict["agent"] = agent
        if materials is not UNSET:
            field_dict["materials"] = materials
        if system_prompt is not UNSET:
            field_dict["system_prompt"] = system_prompt
        if tool_refs is not UNSET:
            field_dict["tool_refs"] = tool_refs
        if skill_refs is not UNSET:
            field_dict["skill_refs"] = skill_refs
        if io_contract is not UNSET:
            field_dict["io_contract"] = io_contract
        if resource_spec is not UNSET:
            field_dict["resource_spec"] = resource_spec
        if capabilities is not UNSET:
            field_dict["capabilities"] = capabilities
        if metadata is not UNSET:
            field_dict["metadata"] = metadata

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.harness_agent_spec import HarnessAgentSpec
        from ..models.harness_materials_spec import HarnessMaterialsSpec
        from ..models.harness_runtime_spec import HarnessRuntimeSpec
        from ..models.harness_spec_metadata import HarnessSpecMetadata
        from ..models.io_contract import IOContract
        from ..models.resource_spec import ResourceSpec
        from ..models.system_prompt_spec import SystemPromptSpec
        from ..models.version_ref import VersionRef

        d = dict(src_dict)
        runtime = HarnessRuntimeSpec.from_dict(d.pop("runtime"))

        ttl_seconds = d.pop("ttl_seconds")

        _agent = d.pop("agent", UNSET)
        agent: HarnessAgentSpec | Unset
        if isinstance(_agent, Unset):
            agent = UNSET
        else:
            agent = HarnessAgentSpec.from_dict(_agent)

        _materials = d.pop("materials", UNSET)
        materials: HarnessMaterialsSpec | Unset
        if isinstance(_materials, Unset):
            materials = UNSET
        else:
            materials = HarnessMaterialsSpec.from_dict(_materials)

        _system_prompt = d.pop("system_prompt", UNSET)
        system_prompt: SystemPromptSpec | Unset
        if isinstance(_system_prompt, Unset):
            system_prompt = UNSET
        else:
            system_prompt = SystemPromptSpec.from_dict(_system_prompt)

        _tool_refs = d.pop("tool_refs", UNSET)
        tool_refs: list[VersionRef] | Unset = UNSET
        if _tool_refs is not UNSET:
            tool_refs = []
            for tool_refs_item_data in _tool_refs:
                tool_refs_item = VersionRef.from_dict(tool_refs_item_data)

                tool_refs.append(tool_refs_item)

        _skill_refs = d.pop("skill_refs", UNSET)
        skill_refs: list[VersionRef] | Unset = UNSET
        if _skill_refs is not UNSET:
            skill_refs = []
            for skill_refs_item_data in _skill_refs:
                skill_refs_item = VersionRef.from_dict(skill_refs_item_data)

                skill_refs.append(skill_refs_item)

        _io_contract = d.pop("io_contract", UNSET)
        io_contract: IOContract | Unset
        if isinstance(_io_contract, Unset):
            io_contract = UNSET
        else:
            io_contract = IOContract.from_dict(_io_contract)

        _resource_spec = d.pop("resource_spec", UNSET)
        resource_spec: ResourceSpec | Unset
        if isinstance(_resource_spec, Unset):
            resource_spec = UNSET
        else:
            resource_spec = ResourceSpec.from_dict(_resource_spec)

        capabilities = cast(list[str], d.pop("capabilities", UNSET))

        _metadata = d.pop("metadata", UNSET)
        metadata: HarnessSpecMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = HarnessSpecMetadata.from_dict(_metadata)

        harness_spec = cls(
            runtime=runtime,
            ttl_seconds=ttl_seconds,
            agent=agent,
            materials=materials,
            system_prompt=system_prompt,
            tool_refs=tool_refs,
            skill_refs=skill_refs,
            io_contract=io_contract,
            resource_spec=resource_spec,
            capabilities=capabilities,
            metadata=metadata,
        )

        harness_spec.additional_properties = d
        return harness_spec

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
