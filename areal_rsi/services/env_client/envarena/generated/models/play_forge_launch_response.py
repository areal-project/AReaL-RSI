from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.play_forge_game_agent_selector import PlayForgeGameAgentSelector


T = TypeVar("T", bound="PlayForgeLaunchResponse")


@_attrs_define
class PlayForgeLaunchResponse:
    """
    Attributes:
        stream_id (str):
        job_id (str):
        task_id (str):
        status (str):
        sandbox_url (str): Pre-signed ARCA session URL for sandbox port 8080. Treat it as a bearer capability and do not
            expose it in logs, traces, or UI.
        ttl_minutes (int):
        runtime_profile (str):
        template_id (str):
        gameagent (PlayForgeGameAgentSelector):
        harness_system_prompt_path (str): Effective in-sandbox system prompt file path. Empty when no Harness system
            prompt OSS file was supplied.
        effective_output_oss_path (str): Effective storage-normalized release directory mounted at `/output`.
        effective_output_storage_ref (str): Effective EnvArena OSS storage reference for effective_output_oss_path,
            selected by the PlayForge runtime profile.
    """

    stream_id: str
    job_id: str
    task_id: str
    status: str
    sandbox_url: str
    ttl_minutes: int
    runtime_profile: str
    template_id: str
    gameagent: PlayForgeGameAgentSelector
    harness_system_prompt_path: str
    effective_output_oss_path: str
    effective_output_storage_ref: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        stream_id = self.stream_id

        job_id = self.job_id

        task_id = self.task_id

        status = self.status

        sandbox_url = self.sandbox_url

        ttl_minutes = self.ttl_minutes

        runtime_profile = self.runtime_profile

        template_id = self.template_id

        gameagent = self.gameagent.to_dict()

        harness_system_prompt_path = self.harness_system_prompt_path

        effective_output_oss_path = self.effective_output_oss_path

        effective_output_storage_ref = self.effective_output_storage_ref

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "stream_id": stream_id,
                "job_id": job_id,
                "task_id": task_id,
                "status": status,
                "sandbox_url": sandbox_url,
                "ttl_minutes": ttl_minutes,
                "runtime_profile": runtime_profile,
                "template_id": template_id,
                "gameagent": gameagent,
                "harness_system_prompt_path": harness_system_prompt_path,
                "effective_output_oss_path": effective_output_oss_path,
                "effective_output_storage_ref": effective_output_storage_ref,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.play_forge_game_agent_selector import PlayForgeGameAgentSelector

        d = dict(src_dict)
        stream_id = d.pop("stream_id")

        job_id = d.pop("job_id")

        task_id = d.pop("task_id")

        status = d.pop("status")

        sandbox_url = d.pop("sandbox_url")

        ttl_minutes = d.pop("ttl_minutes")

        runtime_profile = d.pop("runtime_profile")

        template_id = d.pop("template_id")

        gameagent = PlayForgeGameAgentSelector.from_dict(d.pop("gameagent"))

        harness_system_prompt_path = d.pop("harness_system_prompt_path")

        effective_output_oss_path = d.pop("effective_output_oss_path")

        effective_output_storage_ref = d.pop("effective_output_storage_ref")

        play_forge_launch_response = cls(
            stream_id=stream_id,
            job_id=job_id,
            task_id=task_id,
            status=status,
            sandbox_url=sandbox_url,
            ttl_minutes=ttl_minutes,
            runtime_profile=runtime_profile,
            template_id=template_id,
            gameagent=gameagent,
            harness_system_prompt_path=harness_system_prompt_path,
            effective_output_oss_path=effective_output_oss_path,
            effective_output_storage_ref=effective_output_storage_ref,
        )

        play_forge_launch_response.additional_properties = d
        return play_forge_launch_response

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
