from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.launch_one_task_request_envs import LaunchOneTaskRequestEnvs
    from ..models.launch_one_task_request_metadata import LaunchOneTaskRequestMetadata
    from ..models.version_ref import VersionRef


T = TypeVar("T", bound="LaunchOneTaskRequest")


@_attrs_define
class LaunchOneTaskRequest:
    """
    Attributes:
        data_id (str):  Example: env:tau3-retail-001@0.1.0.
        base_url (str): AReaL proxy base URL; injected for OpenAI and Anthropic-compatible clients.
        api_key (str):
        job_id (str | Unset):
        model_name (str | Unset):
        harness (str | Unset):  Example: tau3-runner@0.1.0.
        reward (str | Unset):  Example: tau3-reward@0.1.0.
        harness_ref (VersionRef | Unset):
        reward_ref (VersionRef | Unset):
        envs (LaunchOneTaskRequestEnvs | Unset):
        metadata (LaunchOneTaskRequestMetadata | Unset):
    """

    data_id: str
    base_url: str
    api_key: str
    job_id: str | Unset = UNSET
    model_name: str | Unset = UNSET
    harness: str | Unset = UNSET
    reward: str | Unset = UNSET
    harness_ref: VersionRef | Unset = UNSET
    reward_ref: VersionRef | Unset = UNSET
    envs: LaunchOneTaskRequestEnvs | Unset = UNSET
    metadata: LaunchOneTaskRequestMetadata | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data_id = self.data_id

        base_url = self.base_url

        api_key = self.api_key

        job_id = self.job_id

        model_name = self.model_name

        harness = self.harness

        reward = self.reward

        harness_ref: dict[str, Any] | Unset = UNSET
        if not isinstance(self.harness_ref, Unset):
            harness_ref = self.harness_ref.to_dict()

        reward_ref: dict[str, Any] | Unset = UNSET
        if not isinstance(self.reward_ref, Unset):
            reward_ref = self.reward_ref.to_dict()

        envs: dict[str, Any] | Unset = UNSET
        if not isinstance(self.envs, Unset):
            envs = self.envs.to_dict()

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "data_id": data_id,
                "base_url": base_url,
                "api_key": api_key,
            }
        )
        if job_id is not UNSET:
            field_dict["job_id"] = job_id
        if model_name is not UNSET:
            field_dict["model_name"] = model_name
        if harness is not UNSET:
            field_dict["harness"] = harness
        if reward is not UNSET:
            field_dict["reward"] = reward
        if harness_ref is not UNSET:
            field_dict["harness_ref"] = harness_ref
        if reward_ref is not UNSET:
            field_dict["reward_ref"] = reward_ref
        if envs is not UNSET:
            field_dict["envs"] = envs
        if metadata is not UNSET:
            field_dict["metadata"] = metadata

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.launch_one_task_request_envs import LaunchOneTaskRequestEnvs
        from ..models.launch_one_task_request_metadata import (
            LaunchOneTaskRequestMetadata,
        )
        from ..models.version_ref import VersionRef

        d = dict(src_dict)
        data_id = d.pop("data_id")

        base_url = d.pop("base_url")

        api_key = d.pop("api_key")

        job_id = d.pop("job_id", UNSET)

        model_name = d.pop("model_name", UNSET)

        harness = d.pop("harness", UNSET)

        reward = d.pop("reward", UNSET)

        _harness_ref = d.pop("harness_ref", UNSET)
        harness_ref: VersionRef | Unset
        if isinstance(_harness_ref, Unset):
            harness_ref = UNSET
        else:
            harness_ref = VersionRef.from_dict(_harness_ref)

        _reward_ref = d.pop("reward_ref", UNSET)
        reward_ref: VersionRef | Unset
        if isinstance(_reward_ref, Unset):
            reward_ref = UNSET
        else:
            reward_ref = VersionRef.from_dict(_reward_ref)

        _envs = d.pop("envs", UNSET)
        envs: LaunchOneTaskRequestEnvs | Unset
        if isinstance(_envs, Unset):
            envs = UNSET
        else:
            envs = LaunchOneTaskRequestEnvs.from_dict(_envs)

        _metadata = d.pop("metadata", UNSET)
        metadata: LaunchOneTaskRequestMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = LaunchOneTaskRequestMetadata.from_dict(_metadata)

        launch_one_task_request = cls(
            data_id=data_id,
            base_url=base_url,
            api_key=api_key,
            job_id=job_id,
            model_name=model_name,
            harness=harness,
            reward=reward,
            harness_ref=harness_ref,
            reward_ref=reward_ref,
            envs=envs,
            metadata=metadata,
        )

        launch_one_task_request.additional_properties = d
        return launch_one_task_request

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
