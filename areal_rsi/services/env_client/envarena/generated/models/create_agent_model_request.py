from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.create_agent_model_request_profile import CreateAgentModelRequestProfile
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.create_agent_model_request_env import CreateAgentModelRequestEnv
    from ..models.create_agent_model_request_metadata import (
        CreateAgentModelRequestMetadata,
    )
    from ..models.create_agent_model_request_parameters import (
        CreateAgentModelRequestParameters,
    )


T = TypeVar("T", bound="CreateAgentModelRequest")


@_attrs_define
class CreateAgentModelRequest:
    """
    Attributes:
        key (str): Model registry key to create or update.
        base_url (str): Gateway or provider base URL injected into generated env.
        api_key (str): Gateway or provider API key injected into generated env.
        version (str | Unset):  Default: '1.0.0'.
        name (str | Unset): Display name. Defaults to key.
        description (str | Unset):
        tags (list[str] | Unset):
        metadata (CreateAgentModelRequestMetadata | Unset):
        supersedes (str | Unset):
        storage_ref (str | Unset): Registry material storage binding. `default` is the immutable OSS_* environment
            configuration.
        model_name (str | Unset): Runtime model name. Defaults to key.
        profile (CreateAgentModelRequestProfile | Unset): claude_code generates ANTHROPIC_* plus Claude model aliases;
            openai generates OPENAI_*; dual generates both. Default: CreateAgentModelRequestProfile.DUAL.
        parameters (CreateAgentModelRequestParameters | Unset):
        env (CreateAgentModelRequestEnv | Unset): Additional env overrides merged after generated values.
        publish (bool | Unset): When false, keep the generated Model version as DRAFT. Default: True.
    """

    key: str
    base_url: str
    api_key: str
    version: str | Unset = "1.0.0"
    name: str | Unset = UNSET
    description: str | Unset = UNSET
    tags: list[str] | Unset = UNSET
    metadata: CreateAgentModelRequestMetadata | Unset = UNSET
    supersedes: str | Unset = UNSET
    storage_ref: str | Unset = UNSET
    model_name: str | Unset = UNSET
    profile: CreateAgentModelRequestProfile | Unset = (
        CreateAgentModelRequestProfile.DUAL
    )
    parameters: CreateAgentModelRequestParameters | Unset = UNSET
    env: CreateAgentModelRequestEnv | Unset = UNSET
    publish: bool | Unset = True
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        key = self.key

        base_url = self.base_url

        api_key = self.api_key

        version = self.version

        name = self.name

        description = self.description

        tags: list[str] | Unset = UNSET
        if not isinstance(self.tags, Unset):
            tags = self.tags

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

        supersedes = self.supersedes

        storage_ref = self.storage_ref

        model_name = self.model_name

        profile: str | Unset = UNSET
        if not isinstance(self.profile, Unset):
            profile = self.profile.value

        parameters: dict[str, Any] | Unset = UNSET
        if not isinstance(self.parameters, Unset):
            parameters = self.parameters.to_dict()

        env: dict[str, Any] | Unset = UNSET
        if not isinstance(self.env, Unset):
            env = self.env.to_dict()

        publish = self.publish

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "key": key,
                "base_url": base_url,
                "api_key": api_key,
            }
        )
        if version is not UNSET:
            field_dict["version"] = version
        if name is not UNSET:
            field_dict["name"] = name
        if description is not UNSET:
            field_dict["description"] = description
        if tags is not UNSET:
            field_dict["tags"] = tags
        if metadata is not UNSET:
            field_dict["metadata"] = metadata
        if supersedes is not UNSET:
            field_dict["supersedes"] = supersedes
        if storage_ref is not UNSET:
            field_dict["storage_ref"] = storage_ref
        if model_name is not UNSET:
            field_dict["model_name"] = model_name
        if profile is not UNSET:
            field_dict["profile"] = profile
        if parameters is not UNSET:
            field_dict["parameters"] = parameters
        if env is not UNSET:
            field_dict["env"] = env
        if publish is not UNSET:
            field_dict["publish"] = publish

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.create_agent_model_request_env import CreateAgentModelRequestEnv
        from ..models.create_agent_model_request_metadata import (
            CreateAgentModelRequestMetadata,
        )
        from ..models.create_agent_model_request_parameters import (
            CreateAgentModelRequestParameters,
        )

        d = dict(src_dict)
        key = d.pop("key")

        base_url = d.pop("base_url")

        api_key = d.pop("api_key")

        version = d.pop("version", UNSET)

        name = d.pop("name", UNSET)

        description = d.pop("description", UNSET)

        tags = cast(list[str], d.pop("tags", UNSET))

        _metadata = d.pop("metadata", UNSET)
        metadata: CreateAgentModelRequestMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = CreateAgentModelRequestMetadata.from_dict(_metadata)

        supersedes = d.pop("supersedes", UNSET)

        storage_ref = d.pop("storage_ref", UNSET)

        model_name = d.pop("model_name", UNSET)

        _profile = d.pop("profile", UNSET)
        profile: CreateAgentModelRequestProfile | Unset
        if isinstance(_profile, Unset):
            profile = UNSET
        else:
            profile = CreateAgentModelRequestProfile(_profile)

        _parameters = d.pop("parameters", UNSET)
        parameters: CreateAgentModelRequestParameters | Unset
        if isinstance(_parameters, Unset):
            parameters = UNSET
        else:
            parameters = CreateAgentModelRequestParameters.from_dict(_parameters)

        _env = d.pop("env", UNSET)
        env: CreateAgentModelRequestEnv | Unset
        if isinstance(_env, Unset):
            env = UNSET
        else:
            env = CreateAgentModelRequestEnv.from_dict(_env)

        publish = d.pop("publish", UNSET)

        create_agent_model_request = cls(
            key=key,
            base_url=base_url,
            api_key=api_key,
            version=version,
            name=name,
            description=description,
            tags=tags,
            metadata=metadata,
            supersedes=supersedes,
            storage_ref=storage_ref,
            model_name=model_name,
            profile=profile,
            parameters=parameters,
            env=env,
            publish=publish,
        )

        create_agent_model_request.additional_properties = d
        return create_agent_model_request

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
