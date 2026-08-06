"""EnvArena contract, configuration, and generated-SDK-backed adapter."""

from __future__ import annotations

import os
import re
import json
import time
import warnings
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx
import yaml

from areal_rsi.schemas.core import EnvArenaEvalRequest, EvalJob, EvalRequest, Traj

from ..base import EnvClient

from .generated.api.jobs import post_openapi_v1_jobs
from .generated.api.llm import get_openapi_v1_llm_models
from .generated.api.model_services import (
    get_openapi_v1_model_services_catalog_models_model_id,
    get_openapi_v1_model_services_catalog_models_model_id_config,
    post_openapi_v1_model_services_service_id_instances_instance_id_stop,
)
from .generated.api.models import post_openapi_v1_models_register
from .generated.api.online import (
    get_openapi_v1_online_harnesses,
    post_openapi_v1_streams_stream_id_launch_batch_task,
)
from .generated.client import AuthenticatedClient
from .generated.models.concurrency_policy import ConcurrencyPolicy
from .generated.models.create_agent_model_request import CreateAgentModelRequest
from .generated.models.create_agent_model_request_env import CreateAgentModelRequestEnv
from .generated.models.create_agent_model_request_metadata import (
    CreateAgentModelRequestMetadata,
)
from .generated.models.create_agent_model_request_parameters import (
    CreateAgentModelRequestParameters,
)
from .generated.models.create_agent_model_request_profile import (
    CreateAgentModelRequestProfile,
)
from .generated.models.create_job_request import CreateJobRequest
from .generated.models.launch_batch_task_item import LaunchBatchTaskItem
from .generated.models.launch_batch_task_request import LaunchBatchTaskRequest
from .generated.models.launch_batch_task_request_metadata import (
    LaunchBatchTaskRequestMetadata,
)
from .generated.models.model_spec import ModelSpec
from .generated.models.version_ref import VersionRef
from .generated.types import UNSET


class EnvArenaConfigurationError(ValueError):
    """Raised when an EnvArena client configuration is incomplete or invalid."""


@dataclass(frozen=True, slots=True)
class EnvArenaVersionRef:
    """A published EnvArena registry version reference."""

    key: str
    version: str

    def __post_init__(self) -> None:
        if not self.key.strip() or not self.version.strip():
            raise EnvArenaConfigurationError("Version references require non-empty key and version.")

    def to_dict(self) -> dict[str, str]:
        return {"key": self.key, "version": self.version}

    @classmethod
    def parse(cls, value: str | Mapping[str, Any]) -> "EnvArenaVersionRef":
        if isinstance(value, Mapping):
            return cls(key=str(value.get("key", "")), version=str(value.get("version", "")))
        key, separator, version = value.rpartition("@")
        if not separator:
            raise EnvArenaConfigurationError(
                f"Version reference {value!r} must use 'key@version' or a {{key, version}} mapping."
            )
        return cls(key=key, version=version)


@dataclass(frozen=True, slots=True)
class EnvArenaConfig:
    """Runtime configuration for EnvArena's authenticated control plane."""

    control_base_url: str
    api_token: str
    timeout_seconds: float = 30.0
    verify_ssl: bool = True
    model_instance_name_prefix: str = "areal-rsi-"
    job_config: Mapping[str, Any] = field(default_factory=dict)
    model_service_config: Mapping[str, Any] = field(default_factory=dict)
    stream_eval_config: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.control_base_url.strip():
            raise EnvArenaConfigurationError("envarena.control_base_url is required.")
        if not self.api_token.strip():
            raise EnvArenaConfigurationError("EnvArena API token is required.")
        if self.timeout_seconds <= 0:
            raise EnvArenaConfigurationError("timeout_seconds must be positive.")
        if not self.model_instance_name_prefix.strip():
            raise EnvArenaConfigurationError("model_instance_name_prefix must be non-empty.")
        if not isinstance(self.job_config, Mapping):
            raise EnvArenaConfigurationError("envarena.job_config must be a mapping when set.")
        if not isinstance(self.model_service_config, Mapping):
            raise EnvArenaConfigurationError("envarena.model_service_config must be a mapping when set.")
        if not isinstance(self.stream_eval_config, Mapping):
            raise EnvArenaConfigurationError("envarena.stream_eval_config must be a mapping when set.")
        object.__setattr__(self, "job_config", deepcopy(dict(self.job_config)))
        object.__setattr__(self, "model_service_config", deepcopy(dict(self.model_service_config)))
        object.__setattr__(self, "stream_eval_config", deepcopy(dict(self.stream_eval_config)))

    @property
    def sdk_base_url(self) -> str:
        """Return the base before the generated SDK's hard-coded `/openapi/v1` path."""

        base_url = self.control_base_url.rstrip("/")
        suffix = "/openapi/v1"
        return base_url[: -len(suffix)] if base_url.endswith(suffix) else base_url

    @classmethod
    def from_yaml(
        cls, path: str | Path, *, environ: Mapping[str, str] | None = None
    ) -> "EnvArenaConfig":
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise EnvArenaConfigurationError("YAML configuration must contain a top-level mapping.")
        section = payload.get("envarena")
        if not isinstance(section, Mapping):
            raise EnvArenaConfigurationError("Configuration must define an envarena YAML mapping.")
        environment = os.environ if environ is None else environ
        control_base_url = str(
            section.get("control_base_url")
            or environment.get(str(section.get("control_base_url_env", "ENVARENA_CONTROL_BASE_URL")), "")
        )
        token = str(
            section.get("api_token")
            or environment.get(str(section.get("token_env", "ENVARENA_API_TOKEN")), "")
        )
        legacy_defaults = {"harness", "benchmarks"}.intersection(section)
        if legacy_defaults:
            names = ", ".join(sorted(legacy_defaults))
            raise EnvArenaConfigurationError(
                f"envarena.{names} defaults are no longer supported; "
                "pass published Harness and Benchmark refs with each evaluation."
            )
        return cls(
            control_base_url=control_base_url,
            api_token=token,
            timeout_seconds=float(section.get("timeout_seconds", 30)),
            verify_ssl=bool(section.get("verify_ssl", True)),
            model_instance_name_prefix=str(section.get("model_instance_name_prefix", "areal-rsi-")),
            job_config=section.get("job_config", {}),
            model_service_config=section.get("model_service_config", section.get("model_service", {})),
            stream_eval_config=section.get("stream_eval_config", section.get("stream_eval", {})),
        )


class EnvArenaError(RuntimeError):
    """Base error raised by the real EnvArena adapter."""


class EnvArenaHTTPError(EnvArenaError):
    """An EnvArena control-plane response outside the operation's success codes."""

    def __init__(self, operation: str, status_code: int, detail: Any):
        self.operation, self.status_code, self.detail = operation, status_code, detail
        super().__init__(f"EnvArena {operation} failed with HTTP {status_code}: {detail}")


_REGISTRY_COLLECTIONS = {
    "env": "envs",
    "envs": "envs",
    "harness": "harnesses",
    "harnesses": "harnesses",
    "reward": "rewards",
    "rewards": "rewards",
    "benchmark": "benchmarks",
    "benchmarks": "benchmarks",
    "model": "models",
    "models": "models",
    "skill": "skills",
    "skills": "skills",
    "tool": "tools",
    "tools": "tools",
}

_COLLECTION_SINGULAR = {
    "envs": "env",
    "harnesses": "harness",
    "rewards": "reward",
    "benchmarks": "benchmark",
    "models": "model",
    "skills": "skill",
    "tools": "tool",
}


class EnvArenaClient(EnvClient):
    """EnvArena control-plane implementation of the generic EnvClient contract.

    Mutating operations use generated request models. Registry list responses
    use raw JSON because openapi-python-client 0.28.0 does not parse the
    OpenAPI `allOf` list response as a 200 response.
    """

    def __init__(
        self,
        config: EnvArenaConfig,
        *,
        sdk_client: AuthenticatedClient | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        self._client = sdk_client or AuthenticatedClient(
            base_url=config.sdk_base_url,
            token=config.api_token,
            timeout=httpx.Timeout(config.timeout_seconds),
            verify_ssl=config.verify_ssl,
            raise_on_unexpected_status=False,
        )
        if transport is not None:
            self._client.set_httpx_client(
                httpx.Client(
                    base_url=config.sdk_base_url,
                    headers={"Authorization": f"Bearer {config.api_token}"},
                    timeout=httpx.Timeout(config.timeout_seconds),
                    verify=config.verify_ssl,
                    transport=transport,
                )
            )

    @classmethod
    def from_yaml(cls, path: str | Path, **kwargs: Any) -> "EnvArenaClient":
        return cls(EnvArenaConfig.from_yaml(path), **kwargs)

    def close(self) -> None:
        self._client.get_httpx_client().close()

    @staticmethod
    def _registry_collection(kind: str) -> str:
        collection = _REGISTRY_COLLECTIONS.get(kind.strip().lower())
        if collection is None:
            supported = ", ".join(sorted(_COLLECTION_SINGULAR))
            raise EnvArenaConfigurationError(f"Unsupported EnvArena registry kind {kind!r}; expected one of {supported}.")
        return collection

    def _paged_items(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        operation: str,
        page_size: int = 200,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        offset = 0
        while True:
            page_params = dict(params or {})
            page_params.setdefault("limit", page_size)
            page_params["offset"] = offset
            payload = self._request_json("GET", path, params=page_params, operation=operation)
            page = list(payload.get("items", []))
            items.extend(page)
            offset += len(page)
            total = int(payload.get("total", len(items)))
            if not page or offset >= total:
                return items

    @staticmethod
    def _clean_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
        return {str(key): value for key, value in payload.items() if value is not None}

    def _version_body(
        self,
        *,
        spec: Mapping[str, Any],
        version: str | None = None,
        supersedes: str | None = None,
        storage_ref: str | Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._clean_payload(
            {
                "version": version,
                "spec": dict(spec),
                "supersedes": supersedes,
                "storage_ref": storage_ref,
            }
        )

    def _set_registry_version_status(self, kind: str, key: str, version: str, action: str) -> dict[str, Any]:
        if action not in {"publish", "deprecate", "archive"}:
            raise EnvArenaConfigurationError(f"Unsupported registry version action {action!r}.")
        collection = self._registry_collection(kind)
        return self._request_json(
            "POST",
            f"/openapi/v1/{collection}/{quote(key, safe='')}/versions/{quote(version, safe='')}/{action}",
            operation=f"{action} {collection} version",
            expected_statuses={200},
        )

    def list_registry_entities(
        self,
        kind: str,
        *,
        status: str | None = None,
        tag: Sequence[str] | None = None,
        keys: Sequence[str] | None = None,
        created_by: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        collection = self._registry_collection(kind)
        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        if tag:
            params["tag"] = list(tag)
        if keys:
            params["keys"] = ",".join(keys)
        if created_by:
            params["created_by"] = created_by
        return self._paged_items(
            f"/openapi/v1/{collection}",
            params=params,
            operation=f"list {collection}",
            page_size=limit,
        )

    def list_registry_entity_page(
        self,
        kind: str,
        *,
        status: str | None = None,
        tag: Sequence[str] | None = None,
        keys: Sequence[str] | None = None,
        created_by: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        if limit <= 0:
            raise EnvArenaConfigurationError("Registry page limit must be positive.")
        if offset < 0:
            raise EnvArenaConfigurationError("Registry page offset must be non-negative.")
        collection = self._registry_collection(kind)
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        if tag:
            params["tag"] = list(tag)
        if keys:
            params["keys"] = ",".join(keys)
        if created_by:
            params["created_by"] = created_by
        return self._request_json(
            "GET",
            f"/openapi/v1/{collection}",
            params=params,
            operation=f"list {collection} page",
        )

    def create_registry_entity(
        self,
        kind: str,
        *,
        key: str,
        name: str | None = None,
        description: str | None = None,
        tags: Sequence[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        collection = self._registry_collection(kind)
        body = self._clean_payload(
            {
                "key": key,
                "name": name or key,
                "description": description,
                "tags": list(tags) if tags else None,
                "metadata": self._string_metadata(metadata) if metadata else None,
            }
        )
        return self._request_json(
            "POST",
            f"/openapi/v1/{collection}",
            json_body=body,
            operation=f"create {collection} entity",
            expected_statuses={200, 201},
        )

    def get_registry_entity(self, kind: str, key: str) -> dict[str, Any]:
        collection = self._registry_collection(kind)
        return self._request_json(
            "GET",
            f"/openapi/v1/{collection}/{quote(key, safe='')}",
            operation=f"get {collection} entity",
        )

    def update_registry_entity(
        self,
        kind: str,
        key: str,
        *,
        name: str | None = None,
        description: str | None = None,
        tags: Sequence[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        collection = self._registry_collection(kind)
        body = self._clean_payload(
            {
                "name": name,
                "description": description,
                "tags": list(tags) if tags is not None else None,
                "metadata": self._string_metadata(metadata) if metadata is not None else None,
            }
        )
        return self._request_json(
            "PATCH",
            f"/openapi/v1/{collection}/{quote(key, safe='')}",
            json_body=body,
            operation=f"update {collection} entity",
        )

    def delete_registry_entity(self, kind: str, key: str) -> dict[str, Any]:
        collection = self._registry_collection(kind)
        return self._request_json(
            "DELETE",
            f"/openapi/v1/{collection}/{quote(key, safe='')}",
            operation=f"delete {collection} entity",
            expected_statuses={204},
        )

    def list_registry_versions(
        self,
        kind: str,
        key: str,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        collection = self._registry_collection(kind)
        params = {"status": status} if status else None
        return list(
            self._request_json(
                "GET",
                f"/openapi/v1/{collection}/{quote(key, safe='')}/versions",
                params=params,
                operation=f"list {collection} versions",
            ).get("items", [])
        )

    def list_registry_version_summaries(
        self,
        kind: str,
        *,
        status: str | None = None,
        keys: Sequence[str] | None = None,
        created_by: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        collection = self._registry_collection(kind)
        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        if keys:
            params["keys"] = ",".join(keys)
        if created_by:
            params["created_by"] = created_by
        return self._paged_items(
            f"/openapi/v1/{collection}/versions",
            params=params,
            operation=f"list {collection} version summaries",
            page_size=limit,
        )

    def create_registry_version(
        self,
        kind: str,
        key: str,
        *,
        version: str,
        spec: Mapping[str, Any],
        supersedes: str | None = None,
        storage_ref: str | None = None,
    ) -> dict[str, Any]:
        collection = self._registry_collection(kind)
        body = self._version_body(version=version, spec=spec, supersedes=supersedes, storage_ref=storage_ref)
        return self._request_json(
            "POST",
            f"/openapi/v1/{collection}/{quote(key, safe='')}/versions",
            json_body=body,
            operation=f"create {collection} version",
            expected_statuses={201},
        )

    def get_registry_version(self, kind: str, key: str, version: str) -> dict[str, Any]:
        collection = self._registry_collection(kind)
        return self._request_json(
            "GET",
            f"/openapi/v1/{collection}/{quote(key, safe='')}/versions/{quote(version, safe='')}",
            operation=f"get {collection} version",
        )

    def update_registry_version(
        self,
        kind: str,
        key: str,
        version: str,
        *,
        spec: Mapping[str, Any],
        supersedes: str | None = None,
        storage_ref: str | None = None,
    ) -> dict[str, Any]:
        collection = self._registry_collection(kind)
        body = self._version_body(spec=spec, supersedes=supersedes, storage_ref=storage_ref)
        return self._request_json(
            "PATCH",
            f"/openapi/v1/{collection}/{quote(key, safe='')}/versions/{quote(version, safe='')}",
            json_body=body,
            operation=f"update {collection} version",
        )

    def publish_registry_version(self, kind: str, key: str, version: str) -> dict[str, Any]:
        return self._set_registry_version_status(kind, key, version, "publish")

    def deprecate_registry_version(self, kind: str, key: str, version: str) -> dict[str, Any]:
        return self._set_registry_version_status(kind, key, version, "deprecate")

    def archive_registry_version(self, kind: str, key: str, version: str) -> dict[str, Any]:
        return self._set_registry_version_status(kind, key, version, "archive")

    def delete_registry_version(self, kind: str, key: str, version: str) -> dict[str, Any]:
        collection = self._registry_collection(kind)
        return self._request_json(
            "DELETE",
            f"/openapi/v1/{collection}/{quote(key, safe='')}/versions/{quote(version, safe='')}",
            operation=f"delete {collection} version",
            expected_statuses={204},
        )

    def list_envs(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_registry_entities("env", **kwargs)

    def create_env(self, **kwargs: Any) -> dict[str, Any]:
        return self.create_registry_entity("env", **kwargs)

    def get_env(self, key: str) -> dict[str, Any]:
        return self.get_registry_entity("env", key)

    def update_env(self, key: str, **kwargs: Any) -> dict[str, Any]:
        return self.update_registry_entity("env", key, **kwargs)

    def delete_env(self, key: str) -> dict[str, Any]:
        return self.delete_registry_entity("env", key)

    def list_env_versions(self, key: str, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_registry_versions("env", key, **kwargs)

    def create_env_version(self, key: str, **kwargs: Any) -> dict[str, Any]:
        return self.create_registry_version("env", key, **kwargs)

    def get_env_version(self, key: str, version: str) -> dict[str, Any]:
        return self.get_registry_version("env", key, version)

    def update_env_version(self, key: str, version: str, **kwargs: Any) -> dict[str, Any]:
        return self.update_registry_version("env", key, version, **kwargs)

    def publish_env_version(self, key: str, version: str) -> dict[str, Any]:
        return self.publish_registry_version("env", key, version)

    def create_harness(self, **kwargs: Any) -> dict[str, Any]:
        return self.create_registry_entity("harness", **kwargs)

    def get_harness(self, key: str) -> dict[str, Any]:
        return self.get_registry_entity("harness", key)

    def update_harness(self, key: str, **kwargs: Any) -> dict[str, Any]:
        return self.update_registry_entity("harness", key, **kwargs)

    def delete_harness(self, key: str) -> dict[str, Any]:
        return self.delete_registry_entity("harness", key)

    def create_harness_version(self, key: str, **kwargs: Any) -> dict[str, Any]:
        return self.create_registry_version("harness", key, **kwargs)

    def get_harness_version(self, key: str, version: str) -> dict[str, Any]:
        return self.get_registry_version("harness", key, version)

    def update_harness_version(self, key: str, version: str, **kwargs: Any) -> dict[str, Any]:
        return self.update_registry_version("harness", key, version, **kwargs)

    def publish_harness_version(self, key: str, version: str) -> dict[str, Any]:
        return self.publish_registry_version("harness", key, version)

    def list_rewards(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_registry_entities("reward", **kwargs)

    def create_reward(self, **kwargs: Any) -> dict[str, Any]:
        return self.create_registry_entity("reward", **kwargs)

    def get_reward(self, key: str) -> dict[str, Any]:
        return self.get_registry_entity("reward", key)

    def update_reward(self, key: str, **kwargs: Any) -> dict[str, Any]:
        return self.update_registry_entity("reward", key, **kwargs)

    def delete_reward(self, key: str) -> dict[str, Any]:
        return self.delete_registry_entity("reward", key)

    def list_reward_versions(self, key: str, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_registry_versions("reward", key, **kwargs)

    def create_reward_version(self, key: str, **kwargs: Any) -> dict[str, Any]:
        return self.create_registry_version("reward", key, **kwargs)

    def get_reward_version(self, key: str, version: str) -> dict[str, Any]:
        return self.get_registry_version("reward", key, version)

    def update_reward_version(self, key: str, version: str, **kwargs: Any) -> dict[str, Any]:
        return self.update_registry_version("reward", key, version, **kwargs)

    def publish_reward_version(self, key: str, version: str) -> dict[str, Any]:
        return self.publish_registry_version("reward", key, version)

    def create_benchmark(self, **kwargs: Any) -> dict[str, Any]:
        return self.create_registry_entity("benchmark", **kwargs)

    def get_benchmark(self, key: str) -> dict[str, Any]:
        return self.get_registry_entity("benchmark", key)

    def update_benchmark(self, key: str, **kwargs: Any) -> dict[str, Any]:
        return self.update_registry_entity("benchmark", key, **kwargs)

    def delete_benchmark(self, key: str) -> dict[str, Any]:
        return self.delete_registry_entity("benchmark", key)

    def create_benchmark_version(self, key: str, **kwargs: Any) -> dict[str, Any]:
        return self.create_registry_version("benchmark", key, **kwargs)

    def get_benchmark_version(self, key: str, version: str) -> dict[str, Any]:
        return self.get_registry_version("benchmark", key, version)

    def update_benchmark_version(self, key: str, version: str, **kwargs: Any) -> dict[str, Any]:
        return self.update_registry_version("benchmark", key, version, **kwargs)

    def publish_benchmark_version(self, key: str, version: str) -> dict[str, Any]:
        return self.publish_registry_version("benchmark", key, version)

    def list_model_entities(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_registry_entities("model", **kwargs)

    def create_model(self, **kwargs: Any) -> dict[str, Any]:
        return self.create_registry_entity("model", **kwargs)

    def get_model(self, key: str) -> dict[str, Any]:
        return self.get_registry_entity("model", key)

    def update_model(self, key: str, **kwargs: Any) -> dict[str, Any]:
        return self.update_registry_entity("model", key, **kwargs)

    def delete_model(self, key: str) -> dict[str, Any]:
        return self.delete_registry_entity("model", key)

    def list_model_versions(self, key: str, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_registry_versions("model", key, **kwargs)

    def create_model_version(self, key: str, **kwargs: Any) -> dict[str, Any]:
        return self.create_registry_version("model", key, **kwargs)

    def get_model_version(self, key: str, version: str) -> dict[str, Any]:
        return self.get_registry_version("model", key, version)

    def update_model_version(self, key: str, version: str, **kwargs: Any) -> dict[str, Any]:
        return self.update_registry_version("model", key, version, **kwargs)

    def publish_model_version(self, key: str, version: str) -> dict[str, Any]:
        return self.publish_registry_version("model", key, version)

    def register_agent_model(
        self,
        gateway_model_id: str,
        *,
        key: str | None = None,
        version: str = "1.0.0",
        name: str | None = None,
        description: str | None = None,
        tags: Sequence[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
        supersedes: str | None = None,
        storage_ref: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        profile: str | CreateAgentModelRequestProfile = CreateAgentModelRequestProfile.DUAL,
        parameters: Mapping[str, Any] | None = None,
        env: Mapping[str, str] | None = None,
        publish: bool = True,
    ) -> dict[str, Any]:
        gateway_model_id = gateway_model_id.strip()
        if not gateway_model_id:
            raise EnvArenaConfigurationError("register_agent_model requires a non-empty gateway_model_id.")
        model_key = str(key or self._derived_asset_key(gateway_model_id)).strip()
        if not model_key:
            raise EnvArenaConfigurationError("register_agent_model requires a non-empty model registry key.")
        if not version.strip():
            raise EnvArenaConfigurationError("register_agent_model requires a non-empty version.")

        try:
            profile_model = (
                profile
                if isinstance(profile, CreateAgentModelRequestProfile)
                else CreateAgentModelRequestProfile(str(profile))
            )
        except ValueError as exc:
            supported = ", ".join(item.value for item in CreateAgentModelRequestProfile)
            raise EnvArenaConfigurationError(
                f"Unsupported agent model profile {profile!r}; expected one of {supported}."
            ) from exc

        metadata_model: CreateAgentModelRequestMetadata | Any = UNSET
        if metadata is not None:
            metadata_model = CreateAgentModelRequestMetadata()
            metadata_model.additional_properties = self._string_metadata(metadata)

        parameters_model: CreateAgentModelRequestParameters | Any = UNSET
        if parameters is not None:
            parameters_model = CreateAgentModelRequestParameters()
            parameters_model.additional_properties = dict(parameters)

        env_model: CreateAgentModelRequestEnv | Any = UNSET
        if env is not None:
            env_model = CreateAgentModelRequestEnv()
            env_model.additional_properties = {str(env_key): str(env_value) for env_key, env_value in env.items()}

        body = CreateAgentModelRequest(
            key=model_key,
            version=version,
            name=name if name is not None else UNSET,
            description=description if description is not None else UNSET,
            tags=list(tags) if tags is not None else UNSET,
            metadata=metadata_model,
            supersedes=supersedes if supersedes is not None else UNSET,
            storage_ref=storage_ref if storage_ref is not None else UNSET,
            model_name=gateway_model_id,
            base_url=(base_url or self._default_llm_gateway_base_url()).rstrip("/"),
            api_key=api_key or self.config.api_token,
            profile=profile_model,
            parameters=parameters_model,
            env=env_model,
            publish=publish,
        )
        body.additional_properties["gateway_model_id"] = gateway_model_id
        body.additional_properties["model_id"] = gateway_model_id
        return self._unwrap_generated(
            post_openapi_v1_models_register.sync_detailed(
                client=self._client,
                body=body,
            ),
            "register agent model",
            {201},
        )

    def create_skill(self, **kwargs: Any) -> dict[str, Any]:
        return self.create_registry_entity("skill", **kwargs)

    def get_skill(self, key: str) -> dict[str, Any]:
        return self.get_registry_entity("skill", key)

    def update_skill(self, key: str, **kwargs: Any) -> dict[str, Any]:
        return self.update_registry_entity("skill", key, **kwargs)

    def delete_skill(self, key: str) -> dict[str, Any]:
        return self.delete_registry_entity("skill", key)

    def create_skill_version(self, key: str, **kwargs: Any) -> dict[str, Any]:
        return self.create_registry_version("skill", key, **kwargs)

    def get_skill_version(self, key: str, version: str) -> dict[str, Any]:
        return self.get_registry_version("skill", key, version)

    def update_skill_version(self, key: str, version: str, **kwargs: Any) -> dict[str, Any]:
        return self.update_registry_version("skill", key, version, **kwargs)

    def publish_skill_version(self, key: str, version: str) -> dict[str, Any]:
        return self.publish_registry_version("skill", key, version)

    def list_tools(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_registry_entities("tool", **kwargs)

    def create_tool(self, **kwargs: Any) -> dict[str, Any]:
        return self.create_registry_entity("tool", **kwargs)

    def get_tool(self, key: str) -> dict[str, Any]:
        return self.get_registry_entity("tool", key)

    def update_tool(self, key: str, **kwargs: Any) -> dict[str, Any]:
        return self.update_registry_entity("tool", key, **kwargs)

    def delete_tool(self, key: str) -> dict[str, Any]:
        return self.delete_registry_entity("tool", key)

    def list_tool_versions(self, key: str, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_registry_versions("tool", key, **kwargs)

    def create_tool_version(self, key: str, **kwargs: Any) -> dict[str, Any]:
        return self.create_registry_version("tool", key, **kwargs)

    def get_tool_version(self, key: str, version: str) -> dict[str, Any]:
        return self.get_registry_version("tool", key, version)

    def update_tool_version(self, key: str, version: str, **kwargs: Any) -> dict[str, Any]:
        return self.update_registry_version("tool", key, version, **kwargs)

    def publish_tool_version(self, key: str, version: str) -> dict[str, Any]:
        return self.publish_registry_version("tool", key, version)

    def list_streams(self, *, status: str | None = None) -> list[dict[str, Any]]:
        params = {"status": status} if status else None
        return list(
            self._request_json(
                "GET",
                "/openapi/v1/streams",
                params=params,
                operation="list streams",
            ).get("items", [])
        )

    def create_stream(
        self,
        *,
        name: str,
        task: str,
        stream_id: str | None = None,
        description: str | None = None,
        selector: Mapping[str, Any] | None = None,
        default_harness_ref: EnvArenaVersionRef | Mapping[str, Any] | str | None = None,
        default_reward_ref: EnvArenaVersionRef | Mapping[str, Any] | str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = self._clean_payload(
            {
                "stream_id": stream_id,
                "name": name,
                "description": description,
                "task": task,
                "selector": dict(selector) if selector is not None else None,
                "default_harness_ref": self._as_version_ref(default_harness_ref).to_dict()
                if default_harness_ref is not None
                else None,
                "default_reward_ref": self._as_version_ref(default_reward_ref).to_dict()
                if default_reward_ref is not None
                else None,
                "metadata": self._string_metadata(metadata) if metadata is not None else None,
            }
        )
        return self._request_json(
            "POST",
            "/openapi/v1/streams",
            json_body=body,
            operation="create stream",
            expected_statuses={201},
        )

    def get_stream(self, stream_id: str) -> dict[str, Any]:
        return self._request_json(
            "GET",
            f"/openapi/v1/streams/{quote(stream_id, safe='')}",
            operation="get stream",
        )

    def update_stream(
        self,
        stream_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        task: str | None = None,
        selector: Mapping[str, Any] | None = None,
        default_harness_ref: EnvArenaVersionRef | Mapping[str, Any] | str | None = None,
        default_reward_ref: EnvArenaVersionRef | Mapping[str, Any] | str | None = None,
        status: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = self._clean_payload(
            {
                "name": name,
                "description": description,
                "task": task,
                "selector": dict(selector) if selector is not None else None,
                "default_harness_ref": self._as_version_ref(default_harness_ref).to_dict()
                if default_harness_ref is not None
                else None,
                "default_reward_ref": self._as_version_ref(default_reward_ref).to_dict()
                if default_reward_ref is not None
                else None,
                "status": status,
                "metadata": self._string_metadata(metadata) if metadata is not None else None,
            }
        )
        return self._request_json(
            "PATCH",
            f"/openapi/v1/streams/{quote(stream_id, safe='')}",
            json_body=body,
            operation="update stream",
        )

    def archive_stream(self, stream_id: str) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"/openapi/v1/streams/{quote(stream_id, safe='')}/archive",
            operation="archive stream",
        )

    def delete_stream(self, stream_id: str) -> dict[str, Any]:
        return self._request_json(
            "DELETE",
            f"/openapi/v1/streams/{quote(stream_id, safe='')}",
            operation="delete stream",
            expected_statuses={204},
        )

    def select_stream_dataset(
        self,
        stream_id: str,
        *,
        selector: Mapping[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        stream = self._stream_object(self.get_stream(stream_id))
        fork_items = self._fork_stream_items_from_stream(stream)
        if fork_items is not None:
            page = fork_items[offset : offset + limit]
            return {
                "data_ids": [str(item.get("data_id", "")) for item in page],
                "items": page,
                "count": len(page),
                "total": len(fork_items),
                "offset": offset,
                "limit": limit,
            }
        body = {"selector": dict(selector)} if selector is not None else {}
        return self._request_json(
            "POST",
            f"/openapi/v1/streams/{quote(stream_id, safe='')}/dataset",
            params={"limit": limit, "offset": offset},
            json_body=body,
            operation="select stream dataset",
        )

    def fork_stream(
        self,
        source_stream_id: str,
        items: Sequence[Mapping[str, Any]],
        *,
        stream_id: str | None = None,
        name: str | None = None,
        description: str | None = None,
        default_harness_ref: EnvArenaVersionRef | Mapping[str, Any] | str | None = None,
        default_reward_ref: EnvArenaVersionRef | Mapping[str, Any] | str | None = None,
        selector: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not items:
            raise EnvArenaConfigurationError("fork_stream requires at least one item.")

        source_stream = self._stream_object(self.get_stream(source_stream_id))
        requested_data_ids = [str(item.get("source_data_id") or item.get("data_id") or "") for item in items]
        if any(not data_id for data_id in requested_data_ids):
            raise EnvArenaConfigurationError("fork_stream items require data_id or source_data_id.")
        source_items = self._select_stream_dataset_items(
            source_stream_id,
            requested_data_ids=requested_data_ids,
            selector=selector,
            page_size=max(len(requested_data_ids), 1),
        )
        source_by_data_id = {str(item.get("data_id", "")): item for item in source_items}

        fork_items: list[dict[str, Any]] = []
        harness_refs: list[EnvArenaVersionRef] = []
        for item in items:
            source_data_id = str(item.get("source_data_id") or item.get("data_id") or "")
            source_item = source_by_data_id[source_data_id]
            data_id = str(item.get("data_id") or source_data_id)
            harness_ref = self._resolve_registry_ref("harnesses", item.get("harness_ref", item.get("harness")), "harness")
            harness_refs.append(harness_ref)
            env_ref = self._as_version_ref(item.get("env_ref") or source_item.get("env_ref") or source_data_id.removeprefix("env:"))
            fork_item = {
                "source_stream_id": source_stream_id,
                "source_data_id": source_data_id,
                "data_id": data_id,
                "env_ref": env_ref.to_dict(),
                "harness_ref": harness_ref.to_dict(),
            }
            if item.get("reward_ref") is not None or item.get("reward") is not None:
                fork_item["reward_ref"] = self._resolve_registry_ref(
                    "rewards",
                    item.get("reward_ref", item.get("reward")),
                    "reward",
                ).to_dict()
            fork_items.append(fork_item)

        default_harness = self._fork_default_harness_ref(
            harness_refs,
            explicit_ref=default_harness_ref,
            stream_ref=source_stream.get("default_harness_ref"),
        )
        default_reward = (
            self._as_version_ref(default_reward_ref)
            if default_reward_ref is not None
            else self._as_version_ref(source_stream["default_reward_ref"])
            if source_stream.get("default_reward_ref") is not None
            else None
        )
        fork_stream_id = stream_id or self._derived_asset_key(f"{source_stream_id}-fork")
        stream = self.create_stream(
            stream_id=fork_stream_id,
            name=name or f"{source_stream.get('name', source_stream_id)} fork",
            description=description or f"AReaL-RSI fork of stream {source_stream_id}",
            task=str(source_stream.get("task") or ""),
            selector=dict(source_stream.get("selector", {})),
            default_harness_ref=default_harness,
            default_reward_ref=default_reward,
            metadata={
                **dict(metadata or {}),
                "source_stream_id": source_stream_id,
                "fork_schema": "areal-rsi.fork_stream.v1",
                "fork_items": json.dumps(fork_items, ensure_ascii=True),
                "generated_by": "areal-rsi",
            },
        )
        stream["source_stream_id"] = source_stream_id
        stream["items"] = fork_items
        return stream

    def list_stream_tasks(
        self,
        stream_id: str,
        *,
        status: str = "ALL",
        experiment: str | None = None,
        all_experiments: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "status": status,
            "limit": limit,
            "all_experiments": str(all_experiments).lower(),
        }
        if experiment is not None:
            params["experiment"] = experiment
        return self._paged_items(
            f"/openapi/v1/streams/{quote(stream_id, safe='')}/tasks",
            params=params,
            operation="list stream tasks",
            page_size=limit,
        )

    def get_stream_task(self, stream_id: str, task_id: str) -> dict[str, Any]:
        return self._request_json(
            "GET",
            f"/openapi/v1/streams/{quote(stream_id, safe='')}/tasks/{quote(task_id, safe='')}",
            operation="get stream task",
        )

    def get_stream_stats(
        self,
        stream_id: str,
        *,
        experiment: str | None = None,
        all_experiments: bool = False,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"all_experiments": str(all_experiments).lower()}
        if experiment is not None:
            params["experiment"] = experiment
        return self._request_json(
            "GET",
            f"/openapi/v1/streams/{quote(stream_id, safe='')}/stats",
            params=params,
            operation="get stream stats",
        )

    def list_stream_experiments(self, stream_id: str) -> dict[str, Any]:
        """List experiments associated with one stream.

        Parameters:
            stream_id: Existing EnvArena stream ID.

        Returns:
            Decoded EnvArena response containing experiment names and metadata.
        """

        return self._request_json(
            "GET",
            f"/openapi/v1/streams/{quote(stream_id, safe='')}/experiments",
            operation="list stream experiments",
        )

    def launch_stream_task(
        self,
        stream_id: str,
        *,
        data_id: str,
        base_url: str,
        api_key: str,
        model_name: str | None = None,
        harness_ref: EnvArenaVersionRef | Mapping[str, Any] | str | None = None,
        reward_ref: EnvArenaVersionRef | Mapping[str, Any] | str | None = None,
        job_id: str | None = None,
        envs: Mapping[str, str] | None = None,
        metadata: Mapping[str, Any] | None = None,
        experiment: str | None = None,
    ) -> dict[str, Any]:
        """Launch one stream task against a runtime model endpoint.

        Parameters:
            stream_id: Existing EnvArena stream ID.
            data_id: Stream data ID to evaluate.
            base_url: Runtime model API base URL.
            api_key: Runtime model API key.
            model_name: Optional served model name used by the runtime endpoint.
            harness_ref: Optional harness registry ref. Accepted forms are
                ``EnvArenaVersionRef``, ``"key@version"``, or
                ``{"key": key, "version": version}``.
            reward_ref: Optional reward registry ref in the same accepted forms
                as ``harness_ref``.
            job_id: Optional caller-supplied job ID.
            envs: Optional environment variables passed to the task.
            metadata: Optional metadata stringified into the launch request.
            experiment: Optional experiment name.

        Returns:
            Decoded EnvArena launch-one-task response.
        """

        body = self._clean_payload(
            {
                "job_id": job_id,
                "data_id": data_id,
                "base_url": base_url,
                "api_key": api_key,
                "model_name": model_name,
                "harness_ref": self._as_version_ref(harness_ref).to_dict() if harness_ref is not None else None,
                "reward_ref": self._as_version_ref(reward_ref).to_dict() if reward_ref is not None else None,
                "envs": dict(envs) if envs is not None else None,
                "metadata": self._string_metadata(metadata) if metadata is not None else None,
                "experiment": experiment,
            }
        )
        return self._request_json(
            "POST",
            f"/openapi/v1/streams/{quote(stream_id, safe='')}/launch_one_task",
            json_body=body,
            operation="launch stream task",
            expected_statuses={201, 202},
        )

    def launch_stream_batch_task(
        self,
        stream_id: str,
        *,
        model_ref: EnvArenaVersionRef | Mapping[str, Any] | str,
        tasks: Sequence[Mapping[str, Any] | str],
        harness_ref: EnvArenaVersionRef | Mapping[str, Any] | str | None = None,
        reward_ref: EnvArenaVersionRef | Mapping[str, Any] | str | None = None,
        job_id: str | None = None,
        concurrency_policy: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        experiment: str | None = None,
    ) -> dict[str, Any]:
        """Launch multiple stream tasks through EnvArena's batch task API.

        Parameters:
            stream_id: ID of the existing EnvArena stream to run against.
            model_ref: Published model registry ref. Accepted forms are
                ``EnvArenaVersionRef``, ``"key@version"``, or
                ``{"key": key, "version": version}``.
            tasks: Per-data task specs. Each item may be a data ID string or a
                mapping with ``data_id`` plus optional ``harness_ref``/``harness``
                and ``reward_ref``/``reward`` overrides.
            harness_ref: Optional default harness registry ref for the batch.
                Individual tasks may override it.
            reward_ref: Optional default reward registry ref for the batch.
                Individual tasks may override it.
            job_id: Optional caller-supplied job identifier.
            concurrency_policy: Optional EnvArena concurrency policy mapping,
                for example max parallelism and retry controls.
            metadata: Optional job metadata. Values are stringified before
                sending to the generated SDK model.
            experiment: Optional EnvArena experiment name.

        Returns:
            Decoded EnvArena response containing the accepted job ID, task IDs,
            and raw job payload.
        """

        registry_model_ref = self._as_version_ref(model_ref)
        task_items = [self._launch_batch_task_item(task) for task in tasks]
        if not task_items:
            raise EnvArenaConfigurationError("launch_stream_batch_task requires at least one task.")

        metadata_model: LaunchBatchTaskRequestMetadata | Any = UNSET
        if metadata is not None:
            metadata_model = LaunchBatchTaskRequestMetadata()
            metadata_model.additional_properties = self._string_metadata(metadata)

        body = LaunchBatchTaskRequest(
            model_ref=VersionRef(**registry_model_ref.to_dict()),
            tasks=task_items,
            job_id=job_id if job_id else UNSET,
            harness_ref=VersionRef(**self._as_version_ref(harness_ref).to_dict()) if harness_ref is not None else UNSET,
            reward_ref=VersionRef(**self._as_version_ref(reward_ref).to_dict()) if reward_ref is not None else UNSET,
            concurrency_policy=self._concurrency_policy(concurrency_policy),
            metadata=metadata_model,
            experiment=experiment if experiment else UNSET,
        )
        return self._unwrap_generated(
            post_openapi_v1_streams_stream_id_launch_batch_task.sync_detailed(
                stream_id,
                client=self._client,
                body=body,
            ),
            "launch stream batch task",
            {202},
        )

    def update_stream_data_skill_experience(
        self,
        stream_id: str,
        data_id: str,
        experience: Mapping[str, Any] | str,
        *,
        selector: Mapping[str, Any] | None = None,
        job_config: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create updated skill and harness versions for one stream data item.

        This helper resolves the current harness for ``data_id`` from the
        stream dataset item. If the harness already has a selectable experience
        skill, the function creates a new version of that skill and a new
        harness version pointing to it. If no such skill exists, it creates a
        new skill entity/version and a new harness entity/version for the data
        item.

        Parameters:
            stream_id: Existing EnvArena stream ID.
            data_id: Stream data ID to update, for example
                ``"env:repo__issue@1.0.0"``.
            experience: New experience content. Accepted forms are a non-empty
                string or a mapping with fields such as ``experience``, ``text``,
                ``task_experiences``, ``skill_key``, version overrides, and
                publish/storage metadata.
            selector: Optional selector passed to stream dataset selection.
            job_config: Optional overrides for version naming and defaults,
                including ``experience_skill`` and ``experience_harness``.
            metadata: Optional metadata copied into generated skill/harness
                specs and entities.

        Returns:
            A summary containing previous and new skill/harness refs plus the
            raw create/publish responses.
        """

        merged_job_config = self._merge_job_config(self.config.job_config, self.config.stream_eval_config)
        merged_job_config = self._merge_job_config(merged_job_config, job_config or {})
        stream_payload = self.get_stream(stream_id)
        stream = self._stream_object(stream_payload)
        resolved_stream_id = str(stream.get("stream_id") or stream_id)
        item = self._select_stream_dataset_items(
            resolved_stream_id,
            requested_data_ids=(data_id,),
            selector=selector if selector is not None else merged_job_config.get("selector"),
            page_size=int(merged_job_config.get("dataset_page_size", 1000)),
        )[0]
        resolved_data_id = str(item.get("data_id") or data_id).strip()
        base_harness_ref = self._resolve_stream_harness_ref(stream, merged_job_config)
        current_harness_ref = self._stream_item_ref(
            item,
            field_names=("harness_ref", "harness"),
            collection="harnesses",
            asset_name="harness",
        ) or base_harness_ref
        return self._create_stream_data_experience_harness_version(
            stream_id=self._source_stream_id_for_stream(stream, resolved_stream_id),
            data_id=resolved_data_id,
            harness_ref=current_harness_ref,
            experience=self._stream_experience_config(experience),
            job_config=merged_job_config,
            metadata=metadata or {},
        )

    def _launch_batch_task_item(self, task: Mapping[str, Any] | str) -> LaunchBatchTaskItem:
        """Convert a user task spec into the generated SDK task model.

        Parameters:
            task: Either a data ID string or a mapping. Mapping form requires
                ``data_id`` and may include ``harness_ref``/``harness`` and
                ``reward_ref``/``reward`` as registry refs.

        Returns:
            ``LaunchBatchTaskItem`` with normalized ``VersionRef`` overrides.
        """

        if isinstance(task, str):
            data_id = task
            harness_ref = None
            reward_ref = None
        elif isinstance(task, Mapping):
            data_id = str(task.get("data_id", "")).strip()
            harness_ref = task.get("harness_ref", task.get("harness"))
            reward_ref = task.get("reward_ref", task.get("reward"))
        else:
            raise EnvArenaConfigurationError("launch_stream_batch_task tasks must be strings or mappings.")
        if not data_id:
            raise EnvArenaConfigurationError("launch_stream_batch_task task requires non-empty data_id.")
        return LaunchBatchTaskItem(
            data_id=data_id,
            harness_ref=VersionRef(**self._as_version_ref(harness_ref).to_dict()) if harness_ref is not None else UNSET,
            reward_ref=VersionRef(**self._as_version_ref(reward_ref).to_dict()) if reward_ref is not None else UNSET,
        )

    def list_model_services(self) -> list[dict[str, Any]]:
        """List EnvArena model services.

        Returns:
            List of model service objects from ``/model-services``.
        """

        return list(
            self._request_json(
                "GET",
                "/openapi/v1/model-services",
                operation="list model services",
            ).get("items", [])
        )

    def create_model_service(
        self,
        *,
        served_model_name: str | None = None,
        model_path: str | None = None,
        base_model_id: str | None = None,
        image: str | None = None,
        replicas: int | None = None,
        readiness_policy: str | None = None,
        resource_spec: Mapping[str, Any] | None = None,
        sglang: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a model service and initial model instance in EnvArena.

        Parameters:
            served_model_name: Name exposed by the inference service. May be
                supplied by ``base_model_id`` defaults.
            model_path: Checkpoint/model path to serve. May be supplied by
                ``base_model_id`` defaults.
            base_model_id: Optional catalog model ID. When present, catalog
                defaults are loaded first; explicit function parameters override
                those defaults.
            image: Optional serving image.
            replicas: Optional replica count.
            readiness_policy: Optional backend readiness policy name.
            resource_spec: Optional resource mapping such as GPU and CPU
                requirements.
            sglang: Optional SGLang serving configuration.
            metadata: Optional metadata for the service. Values are stringified
                before sending.

        Returns:
            Created model service payload. Backends may include service and
            instance identifiers in this response.
        """

        config: dict[str, Any] = {}
        if base_model_id:
            config = self._load_model_service_base_config(base_model_id)
        config = self._merge_job_config(
            config,
            self._clean_payload(
                {
                    "served_model_name": served_model_name,
                    "model_path": model_path,
                    "image": image,
                    "replicas": replicas,
                    "readiness_policy": readiness_policy,
                    "resource_spec": dict(resource_spec) if resource_spec is not None else None,
                    "sglang": dict(sglang) if sglang is not None else None,
                    "metadata": dict(metadata) if metadata is not None else None,
                }
            ),
        )
        served_model_name = str(config.get("served_model_name") or "").strip()
        model_path = str(config.get("model_path") or "").strip()
        if not served_model_name:
            raise EnvArenaConfigurationError(
                "create_model_service requires served_model_name unless base_model_id supplies it."
            )
        if not model_path:
            raise EnvArenaConfigurationError(
                "create_model_service requires model_path unless base_model_id supplies it."
            )
        resource_spec = config.get("resource_spec")
        sglang = config.get("sglang")
        metadata = config.get("metadata")
        if resource_spec is not None and not isinstance(resource_spec, Mapping):
            raise EnvArenaConfigurationError("create_model_service resource_spec must be a mapping when set.")
        if sglang is not None and not isinstance(sglang, Mapping):
            raise EnvArenaConfigurationError("create_model_service sglang must be a mapping when set.")
        if metadata is not None and not isinstance(metadata, Mapping):
            raise EnvArenaConfigurationError("create_model_service metadata must be a mapping when set.")
        body = self._clean_payload(
            {
                "served_model_name": served_model_name,
                "model_path": model_path,
                "image": config.get("image"),
                "replicas": config.get("replicas"),
                "readiness_policy": config.get("readiness_policy"),
                "resource_spec": dict(resource_spec) if resource_spec is not None else None,
                "sglang": dict(sglang) if sglang is not None else None,
                "metadata": self._string_metadata(metadata) if metadata is not None else None,
            }
        )
        return self._request_json(
            "POST",
            "/openapi/v1/model-services",
            json_body=body,
            operation="create model service",
            expected_statuses={201},
        )

    def get_model_service_catalog_model(self, model_id: str) -> dict[str, Any]:
        """Fetch catalog details for a base model.

        Parameters:
            model_id: Catalog/base model identifier.

        Returns:
            Catalog model payload, including availability and optional serving
            defaults when provided by EnvArena.
        """

        return self._unwrap_generated(
            get_openapi_v1_model_services_catalog_models_model_id.sync_detailed(
                model_id,
                client=self._client,
            ),
            "get model service catalog model",
            {200},
        )

    def get_model_service_catalog_config(self, model_id: str) -> dict[str, Any]:
        """Read the model directory's config.json for diagnostics.

        This is intentionally not used to derive model-service launch defaults:
        EnvArena API servers and storage are not necessarily co-located, so a
        catalog model can exist even when its filesystem config.json is not
        readable by the API server.
        """

        return self._unwrap_generated(
            get_openapi_v1_model_services_catalog_models_model_id_config.sync_detailed(
                model_id,
                client=self._client,
            ),
            "get model service catalog config",
            {200},
        )

    def _load_model_service_base_config(self, model_id: str) -> dict[str, Any]:
        """Load usable model-service defaults from a catalog model.

        Parameters:
            model_id: Catalog/base model identifier.

        Returns:
            Normalized config mapping. Returns an empty mapping and emits a
            warning when the catalog model cannot be read or is unavailable.
        """

        try:
            payload = self.get_model_service_catalog_model(model_id)
        except (EnvArenaError, httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            warnings.warn(
                f"EnvArena base model {model_id!r} could not be loaded; "
                f"using explicit model service config only: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )
            return {}
        if (
            payload.get("exists") is False
            or payload.get("readable") is False
            or payload.get("weights_exist") is False
            or payload.get("weights_readable") is False
        ):
            warnings.warn(
                f"EnvArena base model {model_id!r} is not available "
                f"(exists={payload.get('exists')}, readable={payload.get('readable')}, "
                f"weights_exist={payload.get('weights_exist')}, weights_readable={payload.get('weights_readable')}); "
                "using explicit model service config only.",
                RuntimeWarning,
                stacklevel=2,
            )
            return {}
        return self._model_service_base_config_from_catalog(payload)

    @staticmethod
    def _model_service_base_config_from_catalog(payload: Mapping[str, Any]) -> dict[str, Any]:
        """Convert catalog model payload into model-service config fields.

        Parameters:
            payload: Catalog model payload. Fields may appear at top level or
                inside a nested ``config`` mapping.

        Returns:
            Config mapping using ``create_model_service`` field names.
        """

        config: dict[str, Any] = {}
        nested = payload.get("config")
        if isinstance(nested, Mapping):
            for source_key, target_key in (
                ("served_model_name", "served_model_name"),
                ("model_name", "served_model_name"),
                ("model_path", "model_path"),
                ("path", "model_path"),
                ("image", "image"),
                ("replicas", "replicas"),
                ("readiness_policy", "readiness_policy"),
                ("resource_spec", "resource_spec"),
                ("sglang", "sglang"),
                ("metadata", "metadata"),
            ):
                if source_key in nested and target_key not in config:
                    config[target_key] = nested[source_key]
        if payload.get("model_name"):
            config["served_model_name"] = payload["model_name"]
        if payload.get("model_path"):
            config["model_path"] = payload["model_path"]
        for key in ("image", "replicas", "readiness_policy", "resource_spec", "sglang", "metadata"):
            if key in payload:
                config[key] = payload[key]
        return config

    def get_model_service(self, service_id: str) -> dict[str, Any]:
        """Fetch one EnvArena model service by ID.

        Parameters:
            service_id: Model service ID, for example ``svc-...``.

        Returns:
            Model service payload, usually including service status and
            instances.
        """

        return self._request_json(
            "GET",
            f"/openapi/v1/model-services/{quote(service_id, safe='')}",
            operation="get model service",
        )

    def stop_model_service(self, service_id: str) -> dict[str, Any]:
        """Stop an entire model service.

        Parameters:
            service_id: Model service ID.

        Returns:
            EnvArena stop-service response.
        """

        return self._request_json(
            "POST",
            f"/openapi/v1/model-services/{quote(service_id, safe='')}/stop",
            operation="stop model service",
        )

    def stop_model_service_instance(self, service_id: str, instance_id: str) -> dict[str, Any]:
        """Stop one model service instance without stopping the whole service.

        Parameters:
            service_id: Parent model service ID.
            instance_id: Instance ID to stop, for example ``inst-...``.

        Returns:
            EnvArena stop-instance response.
        """

        return self._unwrap_generated(
            post_openapi_v1_model_services_service_id_instances_instance_id_stop.sync_detailed(
                service_id,
                instance_id,
                client=self._client,
            ),
            "stop model service instance",
            {200},
        )

    def get_model_service_instance_from_service(self, service_id: str, instance_id: str) -> dict[str, Any] | None:
        """Find one instance inside a fetched model service payload.

        Parameters:
            service_id: Parent model service ID.
            instance_id: Instance ID to locate.

        Returns:
            Instance mapping when found, otherwise ``None``.
        """

        return self._model_service_instance_from_payload(self.get_model_service(service_id), instance_id)

    def wait_model_service_instance_status(
        self,
        service_id: str,
        instance_id: str,
        *,
        status: str = "STOPPED",
        timeout_seconds: float = 90,
        poll_seconds: float = 5,
    ) -> dict[str, Any]:
        """Poll until one model service instance reaches a target status.

        Parameters:
            service_id: Parent model service ID.
            instance_id: Instance ID to poll.
            status: Desired status string, default ``"STOPPED"``.
            timeout_seconds: Maximum wait time in seconds.
            poll_seconds: Delay between polls in seconds.

        Returns:
            Latest instance payload once it reaches ``status``.
        """

        if timeout_seconds <= 0:
            raise EnvArenaConfigurationError("timeout_seconds must be positive.")
        if poll_seconds <= 0:
            raise EnvArenaConfigurationError("poll_seconds must be positive.")
        deadline = time.monotonic() + timeout_seconds
        last_instance: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            last_instance = self.get_model_service_instance_from_service(service_id, instance_id)
            if last_instance and str(last_instance.get("status") or "") == status:
                return last_instance
            time.sleep(min(poll_seconds, max(deadline - time.monotonic(), 0)))
        raise EnvArenaError(
            f"EnvArena model service instance {service_id}/{instance_id} did not reach "
            f"status {status!r} within {timeout_seconds} seconds; last={last_instance}"
        )

    def wait_model_service_ready(
        self,
        service_id: str,
        *,
        timeout_seconds: float = 1800,
        poll_seconds: float = 15,
        ready_statuses: Sequence[str] = ("READY",),
        terminal_statuses: Sequence[str] = ("FAILED", "STOPPED"),
    ) -> dict[str, Any]:
        """Poll until a model service becomes ready.

        Parameters:
            service_id: Model service ID to poll.
            timeout_seconds: Maximum wait time in seconds.
            poll_seconds: Delay between polls in seconds.
            ready_statuses: Status values treated as successful readiness.
            terminal_statuses: Status values that fail fast.

        Returns:
            Latest model service payload once its status is ready.
        """

        if timeout_seconds <= 0:
            raise EnvArenaConfigurationError("timeout_seconds must be positive.")
        if poll_seconds <= 0:
            raise EnvArenaConfigurationError("poll_seconds must be positive.")
        ready = {str(status) for status in ready_statuses}
        terminal = {str(status) for status in terminal_statuses}
        deadline = time.monotonic() + timeout_seconds
        last_service: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            last_service = self.get_model_service(service_id)
            status = str(last_service.get("status") or "")
            if status in ready:
                return last_service
            if status in terminal:
                raise EnvArenaError(
                    f"EnvArena model service {service_id} reached terminal status {status!r}; "
                    f"service={last_service}"
                )
            time.sleep(min(poll_seconds, max(deadline - time.monotonic(), 0)))
        raise EnvArenaError(
            f"EnvArena model service {service_id} did not reach one of {sorted(ready)} "
            f"within {timeout_seconds} seconds; last={last_service}"
        )

    def stop_model_service_instance_and_wait(
        self,
        service_id: str,
        instance_id: str,
        *,
        status: str = "STOPPED",
        timeout_seconds: float = 90,
        poll_seconds: float = 5,
    ) -> dict[str, Any]:
        """Stop one model instance and wait until it reaches a target status.

        Parameters:
            service_id: Parent model service ID.
            instance_id: Instance ID to stop.
            status: Desired post-stop status, default ``"STOPPED"``.
            timeout_seconds: Maximum wait time in seconds.
            poll_seconds: Delay between status polls in seconds.

        Returns:
            Instance payload once the target status is reached.
        """

        self.stop_model_service_instance(service_id, instance_id)
        return self.wait_model_service_instance_status(
            service_id,
            instance_id,
            status=status,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
        )

    @staticmethod
    def _model_service_instance_from_payload(
        payload: Mapping[str, Any],
        instance_id: str,
    ) -> dict[str, Any] | None:
        """Extract a model service instance from a service payload.

        Parameters:
            payload: Model service payload that may contain an ``instances``
                list.
            instance_id: Instance ID to search for.

        Returns:
            Instance dict when present, otherwise ``None``.
        """

        instances = payload.get("instances")
        if not isinstance(instances, list):
            return None
        for instance in instances:
            if isinstance(instance, Mapping) and str(instance.get("instance_id") or "") == instance_id:
                return dict(instance)
        return None

    def delete_model_service(self, service_id: str) -> dict[str, Any]:
        """Delete a model service.

        Parameters:
            service_id: Model service ID to delete.

        Returns:
            Empty or decoded EnvArena delete response.
        """

        return self._request_json(
            "DELETE",
            f"/openapi/v1/model-services/{quote(service_id, safe='')}",
            operation="delete model service",
            expected_statuses={204},
        )

    @staticmethod
    def _strict_model_config(model: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and normalize the public EnvArena model config.

        Parameters:
            model: Public model JSON object. Only ``type='ref'`` and
                ``type='ckpt'`` are accepted.

        Returns:
            Normalized config with lower-cased ``type``.
        """

        if not isinstance(model, Mapping):
            raise EnvArenaConfigurationError(
                "model must be a JSON object with type='ckpt' or type='ref'."
            )
        config = dict(model)
        kind = str(config.get("type") or "").strip().lower()
        if kind not in {"ckpt", "ref"}:
            raise EnvArenaConfigurationError("model.type must be 'ckpt' or 'ref'.")
        config["type"] = kind
        if kind == "ref":
            ref = config.get("ref")
            if ref is None or (isinstance(ref, str) and not ref.strip()):
                raise EnvArenaConfigurationError("model type='ref' requires non-empty ref.")
            return {"type": "ref", "ref": ref}

        for field_name in ("model_path", "served_model_name", "base_model_id"):
            value = str(config.get(field_name) or "").strip()
            if not value:
                raise EnvArenaConfigurationError(
                    f"model type='ckpt' requires non-empty {field_name}."
                )
            config[field_name] = value
        return config

    def ensure_model_service_for_ckpt(
        self,
        model: Mapping[str, Any],
        *,
        job_config: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Ensure a runtime model service or model registry ref for a model config.

        Parameters:
            model: Strict model config. Accepted forms are
                ``{"type": "ref", "ref": "model@version"}`` for an existing
                model registry version, or ``{"type": "ckpt", "model_path":
                path, "served_model_name": name, "base_model_id": id}`` for a
                checkpoint that should be served/registered. Extra fields are
                treated as model-service overrides.
            job_config: Optional job config. ``model_service`` may override
                default service creation settings and can enable backend service
                creation with ``enabled``/``create``/``create_service``.
            metadata: Optional metadata merged into service creation metadata.

        Returns:
            Mapping with ``model_name`` and ``asset``. When ``model`` is already
            a registry ref, ``registry_ref`` is included and no service is
            started.
        """

        model_config = self._strict_model_config(model)
        if model_config["type"] == "ref":
            registry_ref = self._resolve_registry_ref("models", model_config["ref"], "model")
            return {
                "model_name": registry_ref.key,
                "registry_ref": registry_ref,
                "asset": {
                    "model_name": registry_ref.key,
                    "registry_ref": registry_ref.to_dict(),
                },
            }

        model_service_overrides = (job_config or {}).get("model_service") or {}
        if not isinstance(model_service_overrides, Mapping):
            raise EnvArenaConfigurationError("job_config.model_service must be a mapping when set.")
        model_service_config = self._merge_job_config(
            self.config.model_service_config,
            model_service_overrides,
        )
        model_service_config = self._merge_job_config(model_service_config, model_config)
        base_model_id = str(model_service_config.get("base_model_id") or "").strip()
        model_path = str(model_service_config.get("model_path") or "").strip()
        served_model_name = str(model_service_config.get("served_model_name") or "").strip()
        create_model_service_enabled = bool(
            model_service_config.get("enabled")
            or model_service_config.get("create")
            or model_service_config.get("create_service")
        )
        if create_model_service_enabled:
            service = self.create_model_service(
                served_model_name=served_model_name,
                model_path=model_path,
                base_model_id=base_model_id,
                image=model_service_config.get("image"),
                replicas=model_service_config.get("replicas"),
                readiness_policy=model_service_config.get("readiness_policy"),
                resource_spec=model_service_config.get("resource_spec"),
                sglang=model_service_config.get("sglang"),
                metadata={
                    **dict(metadata or {}),
                    **dict(model_service_config.get("metadata", {})),
                    "source": "areal-rsi",
                },
            )
            return {
                "model_name": str(service.get("served_model_name") or served_model_name),
                "asset": {
                    "model_name": str(service.get("served_model_name") or served_model_name),
                    "model_path": str(service.get("model_path") or model_path),
                    "model_service": service,
                    "status": service.get("status"),
                },
            }
        instance = self._start_model_instance(model_path, {"model_name": served_model_name, **model_service_config})
        return {"model_name": str(instance["model_name"]), "asset": instance}

    def ensure_model_registry_for_ckpt(
        self,
        model: Mapping[str, Any],
        *,
        key: str | None = None,
        version: str | None = None,
        name: str | None = None,
        description: str | None = None,
        tags: Sequence[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
        job_config: Mapping[str, Any] | None = None,
        wait_ready: bool = True,
        ready_timeout_seconds: float = 1800,
        ready_poll_seconds: float = 15,
        ready_statuses: Sequence[str] = ("READY",),
        spec: Mapping[str, Any] | None = None,
        parameters: Mapping[str, Any] | None = None,
        env: Mapping[str, str] | None = None,
        supersedes: str | None = None,
        storage_ref: str | None = None,
        gateway_model_id: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        profile: str | CreateAgentModelRequestProfile | None = None,
        create_entity: bool = True,
        publish: bool = True,
    ) -> dict[str, Any]:
        """Ensure a model registry version exists for a checkpoint/model input.

        The method first resolves or creates a runtime model service through
        ``ensure_model_service_for_ckpt``. If the input already contains a
        registry ref, that ref is returned. Otherwise this method optionally
        waits for the model service to become ready and then registers a model
        version through EnvArena's agent-model registration API, falling back to
        the older model-version API when necessary.

        Parameters:
            model: Strict model config. Use ``type='ref'`` with ``ref`` for
                an existing model registry version, or ``type='ckpt'`` with
                ``model_path``, ``served_model_name``, and ``base_model_id`` for
                a checkpoint that should be served and registered.
            key: Optional registry model key override.
            version: Optional registry model version override.
            name: Optional model entity display name.
            description: Optional model entity description.
            tags: Optional model entity tags.
            metadata: Optional metadata for model service and registry assets.
            job_config: Optional config. ``model_service`` controls service
                creation; ``model_registry`` controls registry defaults.
            wait_ready: Whether to poll model service readiness before
                registering.
            ready_timeout_seconds: Maximum readiness wait time.
            ready_poll_seconds: Delay between readiness polls.
            ready_statuses: Service statuses considered ready.
            spec: Optional registry version spec overrides.
            parameters: Optional model parameters injected into version spec.
            env: Optional environment variables injected into version spec.
            supersedes: Optional superseded model version.
            storage_ref: Optional storage reference for the model version.
            gateway_model_id: Optional gateway model ID passed to the register
                endpoint. Defaults to runtime model name when absent.
            base_url: Optional gateway base URL for agent-model registration.
            api_key: Optional gateway API key for agent-model registration.
            profile: Optional agent model profile enum or string.
            create_entity: Whether to create/register the model entity.
            publish: Whether to publish the created model version.

        Returns:
            Mapping containing ``model_name``, ``registry_ref``, source
            ``asset``, model service details, registry responses, and final
            version spec.
        """

        raw_registry_config = (job_config or {}).get("model_registry") or {}
        if not isinstance(raw_registry_config, Mapping):
            raise EnvArenaConfigurationError("job_config.model_registry must be a mapping when set.")
        registry_config = dict(raw_registry_config)
        service_job_config = self._merge_job_config(job_config or {}, {"model_service": {"enabled": True}})
        resolved_model = self.ensure_model_service_for_ckpt(model, job_config=service_job_config, metadata=metadata)
        existing_ref = resolved_model.get("registry_ref")
        if existing_ref is not None:
            registry_ref = self._as_version_ref(existing_ref)
            return {
                "model_name": resolved_model.get("model_name", ""),
                "registry_ref": registry_ref.to_dict(),
                "asset": resolved_model.get("asset", {}),
            }

        model_name = str(resolved_model.get("model_name") or "").strip()
        if not model_name:
            raise EnvArenaError("Cannot create a model registry version without a runtime model name.")
        asset = dict(resolved_model.get("asset", {}))
        model_service = asset.get("model_service")
        ready_service: dict[str, Any] | None = None
        if isinstance(model_service, Mapping):
            service_id = str(model_service.get("service_id") or "").strip()
            if wait_ready and service_id:
                ready_service = self.wait_model_service_ready(
                    service_id,
                    timeout_seconds=ready_timeout_seconds,
                    poll_seconds=ready_poll_seconds,
                    ready_statuses=ready_statuses,
                )
                model_name = str(ready_service.get("served_model_name") or model_name)
                asset["model_service"] = ready_service

        model_key = str(key or registry_config.get("key") or "").strip()
        if not model_key:
            model_key = self._derived_asset_key(model_name)
        model_version = str(version or registry_config.get("version") or "1.0.0")
        registry_ref = EnvArenaVersionRef(key=model_key, version=model_version)

        version_spec = dict(registry_config.get("spec") or {})
        version_spec.setdefault("model_name", model_name)
        if spec:
            version_spec = self._merge_job_config(version_spec, spec)
        version_spec["model_name"] = str(version_spec.get("model_name") or model_name)
        spec_parameters = parameters if parameters is not None else registry_config.get("parameters")
        if spec_parameters is not None:
            if not isinstance(spec_parameters, Mapping):
                raise EnvArenaConfigurationError("model registry parameters must be a mapping when set.")
            version_spec["parameters"] = self._merge_job_config(
                dict(version_spec.get("parameters", {})),
                spec_parameters,
            )
        spec_env = env if env is not None else registry_config.get("env")
        if spec_env is not None:
            if not isinstance(spec_env, Mapping):
                raise EnvArenaConfigurationError("model registry env must be a mapping when set.")
            version_spec["env"] = {str(env_key): str(env_value) for env_key, env_value in spec_env.items()}

        registry_metadata = {
            **dict(metadata or {}),
            **dict(registry_config.get("metadata", {})),
            "source": "areal-rsi",
            "runtime_model_name": model_name,
        }
        if isinstance(model_service, Mapping) and model_service.get("service_id"):
            registry_metadata["model_service_id"] = str(model_service["service_id"])
        if asset.get("model_path"):
            registry_metadata["model_path"] = str(asset["model_path"])

        if create_entity:
            entity_name = str(name or registry_config.get("name") or model_key)
            entity_description = description if description is not None else registry_config.get("description")
            entity_tags = list(tags if tags is not None else registry_config.get("tags", ["areal-rsi", "model-service"]))
            register_error: Exception | None = None
            try:
                register_payload = self.register_agent_model(
                    str(
                        gateway_model_id
                        or registry_config.get("gateway_model_id")
                        or registry_config.get("model_id")
                        or version_spec.get("model_name")
                        or model_name
                    ),
                    key=model_key,
                    version=model_version,
                    name=entity_name,
                    description=entity_description,
                    tags=entity_tags,
                    metadata=registry_metadata,
                    supersedes=supersedes if supersedes is not None else registry_config.get("supersedes"),
                    storage_ref=storage_ref if storage_ref is not None else registry_config.get("storage_ref"),
                    base_url=base_url or registry_config.get("base_url") or registry_config.get("gateway_base_url"),
                    api_key=api_key or registry_config.get("api_key") or registry_config.get("gateway_api_key"),
                    profile=profile or registry_config.get("profile") or CreateAgentModelRequestProfile.DUAL,
                    parameters=version_spec.get("parameters")
                    if isinstance(version_spec.get("parameters"), Mapping)
                    else None,
                    env=version_spec.get("env") if isinstance(version_spec.get("env"), Mapping) else None,
                    publish=publish,
                )
            except EnvArenaHTTPError as exc:
                if exc.status_code not in {404, 405}:
                    raise
                register_error = exc
            except json.JSONDecodeError as exc:
                register_error = exc

            if register_error is None:
                registry_ref = EnvArenaVersionRef(
                    key=str(register_payload.get("key") or model_key),
                    version=str(register_payload.get("version") or model_version),
                )
                version_payload = register_payload
                publish_payload = register_payload if publish else None
                version_spec = dict(register_payload.get("spec") or version_spec)
                entity_payload: dict[str, Any] | None = None
            else:
                warnings.warn(
                    "EnvArena /openapi/v1/models-register is unavailable or returned a non-JSON error; "
                    f"falling back to legacy model registry creation: {register_error}",
                    RuntimeWarning,
                    stacklevel=2,
                )
                entity_payload = self.create_model(
                    key=model_key,
                    name=entity_name,
                    description=entity_description,
                    tags=entity_tags,
                    metadata=registry_metadata,
                )
                version_payload = self.create_model_version(
                    model_key,
                    version=model_version,
                    spec=version_spec,
                    supersedes=supersedes if supersedes is not None else registry_config.get("supersedes"),
                    storage_ref=storage_ref if storage_ref is not None else registry_config.get("storage_ref"),
                )
                publish_payload = self.publish_model_version(model_key, model_version) if publish else None
        else:
            entity_payload = None
            version_payload = self.create_model_version(
                model_key,
                version=model_version,
                spec=version_spec,
                supersedes=supersedes if supersedes is not None else registry_config.get("supersedes"),
                storage_ref=storage_ref if storage_ref is not None else registry_config.get("storage_ref"),
            )
            publish_payload = self.publish_model_version(model_key, model_version) if publish else None
        return {
            "model_name": model_name,
            "registry_ref": registry_ref.to_dict(),
            "asset": {
                **asset,
                "model_name": model_name,
                "registry_ref": registry_ref.to_dict(),
            },
            "model_service": ready_service or model_service,
            "model_entity": entity_payload,
            "model_version": version_payload,
            "model_publish": publish_payload,
            "model_register": version_payload if create_entity else None,
            "spec": version_spec,
        }

    def submit_eval_by_stream(
        self,
        model: Mapping[str, Any],
        stream_id: str,
        *,
        data_ids: Sequence[str] | None = None,
        skills: Sequence[Mapping[str, Any]] | None = None,
        rewards: Sequence[Mapping[str, Any]] | None = None,
        job_config: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> EvalJob:
        """Submit an evaluation directly against an existing EnvArena stream.

        The method does not create or fork streams. It selects data from
        ``stream_id``, resolves model/harness/reward refs, builds per-data batch
        task overrides, and calls ``launch_stream_batch_task`` on the original
        stream.

        Parameters:
            model: Strict model input. Existing registry model JSON example:
                ``{"type": "ref", "ref": "model-a@1.0.0"}``. Checkpoint model
                JSON example: ``{"type": "ckpt", "model_path": "/models/ckpt",
                "served_model_name": "run-model", "base_model_id":
                "bailing-v3"}``.
            stream_id: Existing EnvArena stream ID. JSON string example:
                ``"swe-bench-verified"``.
            data_ids: Optional ordered subset of stream data IDs. JSON list
                example: ``["env:repo__issue-1@1.0.0",
                "env:repo__issue-2@1.0.0"]``. When omitted or empty, all
                selected data IDs from the stream dataset API are used.
            skills: Optional per-data skill list. Experience JSON example:
                ``[{"data_id": "env:repo__issue-1@1.0.0", "type":
                "experience", "content": "Prefer editing the failing test
                first."}]``. Existing skill JSON example: ``[{"data_id":
                "env:repo__issue-1@1.0.0", "type": "ref", "ref":
                "skill-a@1.0.0"}]``. When provided, every selected data ID must
                have exactly one skill binding.
            rewards: Optional per-data reward list. JSON list example:
                ``[{"data_id": "env:repo__issue-1@1.0.0", "ref":
                "reward-a@1.0.0"}]``. Data IDs without an item use the
                stream/job default reward.
            job_config: Optional submit overrides. JSON object example:
                ``{"selector": {"include_tags": ["train"]},
                "concurrency_policy": {"max_concurrency": 8}, "experiment":
                "rsi-eval", "model_ready_timeout_seconds": 1800}``.
            metadata: Optional metadata copied to model registration and the
                EnvArena batch job. JSON object example:
                ``{"run_id": "run-1", "source": "train_eval"}``.

        Returns:
            ``EvalJob`` with the EnvArena job ID, selected data IDs, model ref,
            default harness/reward refs, task IDs, generated assets, and raw
            launch response.
        """

        return self._submit_stream_eval(
            EnvArenaEvalRequest(
                model=model,
                stream_id=stream_id,
                data_ids=tuple(data_ids or ()),
                skills=tuple(skills or ()),
                rewards=tuple(rewards or ()),
                job_config=dict(job_config or {}),
                metadata=dict(metadata or {}),
            )
        )

    def _submit_stream_eval(self, request: EnvArenaEvalRequest) -> EvalJob:
        """Execute the normalized stream evaluation request.

        Parameters:
            request: Fully normalized ``EnvArenaEvalRequest``. ``request.model``
                describes the model, ``request.stream_id`` points to the source
                stream, ``request.data_ids`` controls selection,
                ``request.skills`` supplies per-data skill ref or experience
                bindings, and ``request.rewards`` supplies per-data reward refs.

        Returns:
            ``EvalJob`` assembled from model registration output and the
            EnvArena batch task response.

        Raises:
            EnvArenaConfigurationError: If required stream/model/harness/reward
                refs are missing, selected data IDs cannot be found, or
                experience/reward bindings are ambiguous.
            EnvArenaError: If EnvArena accepts the request but returns no job ID.
        """

        job_config = self._merge_job_config(self.config.job_config, self.config.stream_eval_config)
        job_config = self._merge_job_config(job_config, request.job_config)
        stream_payload = self.get_stream(request.stream_id)
        stream = self._stream_object(stream_payload)
        stream_id = str(stream.get("stream_id") or request.stream_id)
        source_stream_id = self._source_stream_id_for_stream(stream, stream_id)
        selected_items = self._select_stream_dataset_items(
            stream_id,
            requested_data_ids=request.data_ids,
            selector=job_config.get("selector"),
            page_size=int(job_config.get("dataset_page_size", 1000)),
        )
        selected_data_ids: list[str] = []
        for item in selected_items:
            data_id = str(item.get("data_id", "")).strip()
            if not data_id:
                raise EnvArenaConfigurationError("stream dataset item is missing data_id.")
            selected_data_ids.append(data_id)

        base_harness_ref = self._resolve_stream_harness_ref(stream, job_config)
        reward_ref, reward_refs_by_data_id = self._resolve_stream_reward_bindings(
            stream,
            request.rewards,
            job_config,
            data_ids=selected_data_ids,
        )
        experiences_by_data_id, skill_refs_by_data_id = self._resolve_stream_skill_bindings(
            request.skills,
            data_ids=selected_data_ids,
        )
        global_skill_refs: list[EnvArenaVersionRef] = []

        harness_cache: dict[tuple[tuple[str, str], ...], EnvArenaVersionRef] = {}

        def harness_for_skill_refs(
            skill_refs: Sequence[EnvArenaVersionRef],
            *,
            data_id: str | None = None,
        ) -> EnvArenaVersionRef:
            """Return or create the harness ref for a skill-ref set.

            Parameters:
                skill_refs: Skill refs that should be present in the derived
                    harness.
                data_id: Optional data ID used when deriving task-specific
                    harness keys and metadata.

            Returns:
                Base harness ref when ``skill_refs`` is empty, otherwise a
                cached or newly created derived harness ref.
            """

            key = tuple((ref.key, ref.version) for ref in skill_refs)
            if not key:
                return base_harness_ref
            if key not in harness_cache:
                harness_cache[key] = self._create_fork_harness(
                    base_harness_ref,
                    skill_refs,
                    source_stream_id,
                    job_config,
                    key_seed=(
                        f"{base_harness_ref.key}-{data_id}-stream-harness"
                        if data_id
                        else f"{base_harness_ref.key}-stream-harness"
                    ),
                    extra_metadata={"data_id": data_id} if data_id else None,
                )
            return harness_cache[key]

        harness_ref = (
            harness_for_skill_refs(global_skill_refs)
            if global_skill_refs
            else base_harness_ref
        )
        tasks: list[dict[str, Any]] = []
        all_skill_refs: list[EnvArenaVersionRef] = list(global_skill_refs)
        experience_updates: list[dict[str, Any]] = []
        for item in selected_items:
            data_id = str(item.get("data_id", "")).strip()
            task_harness_ref = self._stream_item_ref(
                item,
                field_names=("harness_ref", "harness"),
                collection="harnesses",
                asset_name="harness",
            )
            if data_id in experiences_by_data_id:
                experience = experiences_by_data_id.get(data_id)
                if experience is None:
                    raise EnvArenaConfigurationError(f"stream data_id {data_id!r} has no bound experience.")
                update = self._create_stream_data_experience_harness_version(
                    stream_id=source_stream_id,
                    data_id=data_id,
                    harness_ref=task_harness_ref or base_harness_ref,
                    experience=experience,
                    job_config=job_config,
                    metadata=request.metadata,
                )
                task_harness_ref = self._as_version_ref(update["harness_ref"])
                all_skill_refs = self._dedupe_refs([*all_skill_refs, self._as_version_ref(update["skill_ref"])])
                experience_updates.append(update)
            else:
                task_skill_refs = self._dedupe_refs(
                    [*global_skill_refs, *skill_refs_by_data_id.get(data_id, [])]
                )
                all_skill_refs = self._dedupe_refs([*all_skill_refs, *task_skill_refs])
                if task_harness_ref is None and task_skill_refs != global_skill_refs:
                    task_harness_ref = harness_for_skill_refs(task_skill_refs, data_id=data_id)

            task_reward_ref = reward_refs_by_data_id.get(data_id)
            if task_reward_ref is None:
                task_reward_ref = self._stream_item_ref(
                    item,
                    field_names=("reward_ref", "reward"),
                    collection="rewards",
                    asset_name="reward",
                )
            task: dict[str, Any] = {"data_id": data_id}
            if task_harness_ref is not None and task_harness_ref != harness_ref:
                task["harness_ref"] = task_harness_ref.to_dict()
            if task_reward_ref is not None and task_reward_ref != reward_ref:
                task["reward_ref"] = task_reward_ref.to_dict()
            tasks.append(task)

        model = self.ensure_model_registry_for_ckpt(
            request.model,
            job_config=job_config,
            metadata={
                **dict(request.metadata),
                "source_stream_id": source_stream_id,
            },
            wait_ready=bool(job_config.get("wait_model_ready", True)),
            ready_timeout_seconds=float(job_config.get("model_ready_timeout_seconds", 1800)),
            ready_poll_seconds=float(job_config.get("model_ready_poll_seconds", 15)),
            parameters=job_config.get("model_parameters"),
            env=job_config.get("model_env"),
        )
        model_ref = self._as_version_ref(model["registry_ref"])
        job_metadata = {
            **dict(request.metadata),
            "source": request.metadata.get("source", "eval"),
            "stream_eval_mode": "stream",
            "source_stream_id": source_stream_id,
        }
        payload = self.launch_stream_batch_task(
            stream_id,
            model_ref=model_ref,
            tasks=tasks,
            harness_ref=harness_ref,
            reward_ref=reward_ref,
            job_id=job_config.get("job_id"),
            concurrency_policy=job_config.get("concurrency_policy"),
            metadata=job_metadata,
            experiment=str(job_config.get("experiment")) if job_config.get("experiment") is not None else None,
        )
        job_payload = payload.get("job", {})
        selected_data_ids = [task["data_id"] for task in tasks]
        job: EvalJob = {
            "eval_job_id": str(payload.get("job_id") or job_payload.get("job_id") or ""),
            "status": job_payload.get("status", "PENDING"),
            "model_ref": {
                "model_name": model.get("model_name") or model_ref.key,
                "runtime_model_name": model.get("model_name") or model_ref.key,
                "registry_ref": model_ref.to_dict(),
            },
            "stream_id": stream_id,
            "source_stream_id": source_stream_id,
            "harness_ref": harness_ref.to_dict(),
            "harness_config": {"harness_ref": harness_ref.to_dict()},
            "reward_ref": reward_ref.to_dict(),
            "task_ids": list(payload.get("task_ids", [])),
            "selected_data_ids": list(selected_data_ids),
            "assets": {
                "model": model["asset"],
                "harness": harness_ref.to_dict(),
                "reward": reward_ref.to_dict(),
                "stream": stream,
                "data_ids": list(selected_data_ids),
                "stream_tasks": tasks,
                **({"skills": [ref.to_dict() for ref in all_skill_refs]} if all_skill_refs else {}),
                **({"experience_updates": experience_updates} if experience_updates else {}),
            },
            "job_config": dict(job_config),
            "metadata": job_metadata,
            "raw": payload,
        }
        if not job["eval_job_id"]:
            raise EnvArenaError(f"EnvArena launch stream batch task returned no job_id: {payload}")
        return job

    @staticmethod
    def _stream_object(payload: Mapping[str, Any]) -> dict[str, Any]:
        """Extract the stream object from an EnvArena get-stream response.

        Parameters:
            payload: Either a raw stream mapping or a wrapper mapping with a
                ``stream`` field.

        Returns:
            Mutable ``dict`` copy of the stream object.
        """

        stream = payload.get("stream", payload)
        if not isinstance(stream, Mapping):
            raise EnvArenaError("EnvArena get stream returned no stream object.")
        return dict(stream)

    @staticmethod
    def _stream_metadata(stream: Mapping[str, Any]) -> dict[str, Any]:
        """Return normalized metadata from a stream object.

        Parameters:
            stream: Stream mapping returned by EnvArena.

        Returns:
            Metadata as a dict. Non-mapping metadata is treated as empty.
        """

        metadata = stream.get("metadata", {})
        return dict(metadata) if isinstance(metadata, Mapping) else {}

    def _source_stream_id_for_stream(self, stream: Mapping[str, Any], stream_id: str) -> str:
        """Resolve the original source stream ID for a stream-like object.

        Parameters:
            stream: Stream mapping that may contain ``source_stream_id`` either
                directly or inside metadata.
            stream_id: Fallback stream ID, usually the requested stream ID.

        Returns:
            The source stream ID if present, otherwise ``stream_id``.
        """

        metadata = self._stream_metadata(stream)
        return str(metadata.get("source_stream_id") or stream.get("source_stream_id") or stream_id)

    def _fork_stream_items_from_stream(self, stream: Mapping[str, Any]) -> list[dict[str, Any]] | None:
        """Decode legacy fork-stream item metadata.

        Parameters:
            stream: Stream object whose metadata may contain JSON-encoded
                ``fork_items``.

        Returns:
            Normalized fork item list when metadata is present, otherwise
            ``None``. This exists for backward compatibility with old fork
            stream records; new submit flow does not create fork streams.
        """

        metadata = self._stream_metadata(stream)
        raw_items = metadata.get("fork_items")
        if raw_items is None:
            return None
        try:
            decoded = json.loads(str(raw_items))
        except ValueError as exc:
            raise EnvArenaError(f"Fork stream {stream.get('stream_id', '')!r} has invalid fork_items metadata.") from exc
        if not isinstance(decoded, list):
            raise EnvArenaError(f"Fork stream {stream.get('stream_id', '')!r} fork_items metadata must be a list.")
        items: list[dict[str, Any]] = []
        for item in decoded:
            if not isinstance(item, Mapping):
                raise EnvArenaError(f"Fork stream {stream.get('stream_id', '')!r} contains a non-object fork item.")
            data_id = str(item.get("data_id", "")).strip()
            env_ref = item.get("env_ref")
            harness_ref = item.get("harness_ref")
            if not data_id or env_ref is None or harness_ref is None:
                raise EnvArenaError(
                    f"Fork stream {stream.get('stream_id', '')!r} items require data_id, env_ref, and harness_ref."
                )
            normalized = dict(item)
            normalized["data_id"] = data_id
            normalized["env_ref"] = self._as_version_ref(env_ref).to_dict()
            normalized["harness_ref"] = self._as_version_ref(harness_ref).to_dict()
            if normalized.get("reward_ref") is not None:
                normalized["reward_ref"] = self._as_version_ref(normalized["reward_ref"]).to_dict()
            items.append(normalized)
        return items

    def _fork_default_harness_ref(
        self,
        harness_refs: Sequence[EnvArenaVersionRef],
        *,
        explicit_ref: EnvArenaVersionRef | Mapping[str, Any] | str | None,
        stream_ref: EnvArenaVersionRef | Mapping[str, Any] | str | None,
    ) -> EnvArenaVersionRef:
        """Choose the default harness ref for legacy fork stream creation.

        Parameters:
            harness_refs: Per-item harness refs.
            explicit_ref: Optional explicit default harness ref.
            stream_ref: Optional source stream default harness ref.

        Returns:
            Explicit ref, sole unique item harness ref, or source stream ref.
        """

        if explicit_ref is not None:
            return self._as_version_ref(explicit_ref)
        unique = self._dedupe_refs(harness_refs)
        if len(unique) == 1:
            return unique[0]
        if stream_ref is None:
            raise EnvArenaConfigurationError("fork_stream requires default_harness_ref when item harness refs differ.")
        return self._as_version_ref(stream_ref)

    def _resolve_stream_harness_ref(
        self,
        stream: Mapping[str, Any],
        job_config: Mapping[str, Any],
    ) -> EnvArenaVersionRef:
        """Resolve the default harness ref for stream evaluation.

        Parameters:
            stream: Stream object that may define ``default_harness_ref``.
            job_config: Merged job config. ``harness_ref`` or ``harness`` takes
                precedence over the stream default.

        Returns:
            Normalized harness ``EnvArenaVersionRef``.
        """

        config_ref = job_config.get("harness_ref", job_config.get("harness"))
        if config_ref is not None:
            return self._resolve_registry_ref("harnesses", config_ref, "harness")
        stream_ref = stream.get("default_harness_ref")
        if stream_ref is None:
            raise EnvArenaConfigurationError(
                "stream evaluation requires harness_ref in job_config or stream.default_harness_ref."
            )
        return self._as_version_ref(stream_ref)

    def _resolve_stream_reward_bindings(
        self,
        stream: Mapping[str, Any],
        rewards_config: Sequence[Mapping[str, Any]],
        job_config: Mapping[str, Any],
        *,
        data_ids: Sequence[str],
    ) -> tuple[EnvArenaVersionRef, dict[str, EnvArenaVersionRef]]:
        """Resolve default and per-data reward refs for stream batch tasks.

        Parameters:
            stream: Stream object that may provide ``default_reward_ref``.
            rewards_config: JSON-list-style reward bindings. Each item must be
                a mapping with ``data_id`` and ``ref``. ``ref`` accepts
                ``"key@version"``, ``{"key": ..., "version": ...}``, or
                ``{"type": "ref", ...}``.
            job_config: Merged job config. ``reward_ref`` or ``reward`` is used
                as the default reward fallback when present.
            data_ids: Selected data IDs. Reward bindings may only target these
                IDs.

        Returns:
            Tuple of ``(default_reward_ref, per_data_reward_refs)``.
        """

        refs_by_data_id = self._stream_reward_config_bindings(rewards_config, data_ids=data_ids)
        default_config = job_config.get("reward_ref", job_config.get("reward"))

        if default_config is not None:
            default_ref = self._resolve_stream_reward_config(default_config)
        else:
            stream_ref = stream.get("default_reward_ref")
            if stream_ref is None:
                raise EnvArenaConfigurationError(
                    "stream evaluation requires rewards/reward_ref in request/job_config or stream.default_reward_ref."
                )
            default_ref = self._as_version_ref(stream_ref)
        return default_ref, refs_by_data_id

    def _stream_reward_config_bindings(
        self,
        config: Sequence[Mapping[str, Any]],
        *,
        data_ids: Sequence[str],
    ) -> dict[str, EnvArenaVersionRef]:
        """Parse JSON-list-style per-data reward bindings.

        Parameters:
            config: Sequence of mappings. Each mapping must contain ``data_id``
                and ``ref``.
            data_ids: Selected data IDs. Bindings outside this set are rejected.

        Returns:
            Mapping from data ID to reward ref.
        """

        refs_by_data_id: dict[str, EnvArenaVersionRef] = {}
        selected = {str(data_id) for data_id in data_ids}
        for item in config:
            if not isinstance(item, Mapping):
                raise EnvArenaConfigurationError("rewards items must be mappings with data_id and ref.")
            data_id = str(item.get("data_id") or "").strip()
            if not data_id:
                raise EnvArenaConfigurationError("rewards items require non-empty data_id.")
            if data_id not in selected:
                raise EnvArenaConfigurationError(f"reward binding for data_id {data_id!r} is not selected.")
            if data_id in refs_by_data_id:
                raise EnvArenaConfigurationError(f"data_id {data_id!r} has multiple reward bindings.")
            if item.get("ref") is None:
                raise EnvArenaConfigurationError(f"reward binding for data_id {data_id!r} requires ref.")
            refs_by_data_id[data_id] = self._resolve_registry_ref("rewards", item["ref"], "reward")
        return refs_by_data_id

    def _resolve_stream_reward_config(self, config: Any) -> EnvArenaVersionRef:
        """Resolve one reward config into a registry version ref.

        Parameters:
            config: Reward ref/config. Accepted forms are ``EnvArenaVersionRef``,
                ``"key@version"``, ``{"key": ..., "version": ...}``,
                ``{"type": "ref", ...}``, or ``{"type": "new", ...}``.

        Returns:
            Published reward version ref. ``type='new'`` configs create the
            reward asset before returning its ref.
        """

        if isinstance(config, Mapping) and config.get("type") in {"new", "ref"}:
            ref = self._resolve_optional_registry_asset("rewards", config)
            if ref is None:
                raise EnvArenaConfigurationError("reward config did not resolve to a registry ref.")
            return ref
        return self._resolve_registry_ref("rewards", config, "reward")

    @staticmethod
    def _looks_like_registry_ref_or_asset_config(config: Mapping[str, Any]) -> bool:
        """Return whether a mapping is a single registry ref or asset config.

        Parameters:
            config: Candidate mapping.

        Returns:
            ``True`` for ``type='new'``/``type='ref'``, ``ref``,
            ``registry_ref``, or direct ``key``/``version`` mappings.
        """

        if str(config.get("type") or "").strip().lower() in {"new", "ref"}:
            return True
        if config.get("ref") is not None or config.get("registry_ref") is not None:
            return True
        return config.get("key") is not None and config.get("version") is not None

    def _resolve_stream_reward_ref(
        self,
        stream: Mapping[str, Any],
        reward_config: Mapping[str, Any] | str | None,
        job_config: Mapping[str, Any],
    ) -> EnvArenaVersionRef:
        """Resolve the legacy single reward ref for stream evaluation.

        Parameters:
            stream: Stream object that may define ``default_reward_ref``.
            reward_config: Optional explicit reward ref/config from older call
                paths. Accepted forms are ``"key@version"``, ``{"key":
                ..., "version": ...}``, or ``type='new'``/``type='ref'`` asset
                config.
            job_config: Merged job config; ``reward_ref`` or ``reward`` is used
                when ``reward_config`` is not provided.

        Returns:
            Normalized reward registry ref.
        """

        config_ref = reward_config if reward_config is not None else job_config.get("reward_ref", job_config.get("reward"))
        if config_ref is not None:
            if isinstance(config_ref, Mapping) and config_ref.get("type") in {"new", "ref"}:
                ref = self._resolve_optional_registry_asset("rewards", config_ref)
                if ref is None:
                    raise EnvArenaConfigurationError("reward config did not resolve to a registry ref.")
                return ref
            return self._resolve_registry_ref("rewards", config_ref, "reward")
        stream_ref = stream.get("default_reward_ref")
        if stream_ref is None:
            raise EnvArenaConfigurationError(
                "stream evaluation requires reward_ref in job_config/reward or stream.default_reward_ref."
            )
        return self._as_version_ref(stream_ref)

    def _select_stream_dataset_items(
        self,
        stream_id: str,
        *,
        requested_data_ids: Sequence[str],
        selector: Mapping[str, Any] | None,
        page_size: int,
    ) -> list[dict[str, Any]]:
        """Select stream dataset items and preserve requested order.

        Parameters:
            stream_id: Existing EnvArena stream ID.
            requested_data_ids: Optional ordered data IDs. When empty, all
                items returned by paginated selection are included.
            selector: Optional EnvArena dataset selector mapping.
            page_size: Number of dataset items requested per page.

        Returns:
            List of selected dataset item mappings. For explicit
            ``requested_data_ids``, the return order matches the request.
        """

        wanted = [str(data_id) for data_id in requested_data_ids]
        wanted_set = set(wanted)
        found: dict[str, dict[str, Any]] = {}
        items: list[dict[str, Any]] = []
        offset = 0
        while True:
            payload = self.select_stream_dataset(stream_id, selector=selector, limit=page_size, offset=offset)
            page = [dict(item) for item in payload.get("items", [])]
            if wanted_set:
                for item in page:
                    data_id = str(item.get("data_id", ""))
                    if data_id in wanted_set:
                        found[data_id] = item
            else:
                items.extend(page)
            offset += len(page)
            total = int(payload.get("total", offset))
            if wanted_set and wanted_set.issubset(found):
                return [found[data_id] for data_id in wanted]
            if not page or offset >= total:
                break
        if wanted_set:
            missing = [data_id for data_id in wanted if data_id not in found]
            if missing:
                raise EnvArenaConfigurationError(
                    f"stream {stream_id!r} did not select requested data_ids: {', '.join(missing)}"
                )
            return [found[data_id] for data_id in wanted]
        if not items:
            raise EnvArenaConfigurationError(f"stream {stream_id!r} selected no data_ids.")
        return items

    def _stream_item_ref(
        self,
        item: Mapping[str, Any],
        *,
        field_names: Sequence[str],
        collection: str,
        asset_name: str,
    ) -> EnvArenaVersionRef | None:
        """Resolve a registry ref from a stream item.

        Parameters:
            item: Stream dataset item. It may carry fields such as
                ``harness_ref`` or ``reward_ref``.
            field_names: Candidate field names to inspect in order.
            collection: Registry collection name, for example ``harnesses`` or
                ``rewards``.
            asset_name: Human-readable asset name used in validation errors.

        Returns:
            Resolved registry ref, or ``None`` when no candidate field is set.
        """

        for field_name in field_names:
            if item.get(field_name) is not None:
                return self._resolve_registry_ref(collection, item[field_name], asset_name)
        return None

    @staticmethod
    def _stream_experience_config(experience: Mapping[str, Any] | str) -> dict[str, Any]:
        """Normalize raw experience input into a mutable mapping.

        Parameters:
            experience: Non-empty string experience text or mapping-shaped
                experience config.

        Returns:
            Experience mapping. String input is wrapped as
            ``{"experience": text}``.
        """

        if isinstance(experience, str):
            text = experience.strip()
            if not text:
                raise EnvArenaConfigurationError("stream experience update text must be non-empty.")
            return {"experience": text}
        if not isinstance(experience, Mapping):
            raise EnvArenaConfigurationError("stream experience update must be a mapping or non-empty string.")
        return dict(experience)

    def _create_stream_data_experience_harness_version(
        self,
        *,
        stream_id: str,
        data_id: str,
        harness_ref: EnvArenaVersionRef,
        experience: Mapping[str, Any],
        job_config: Mapping[str, Any],
        metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Create skill and harness versions for one data item's experience.

        Parameters:
            stream_id: Source stream ID used for generated metadata.
            data_id: Stream data ID receiving the new experience.
            harness_ref: Current harness version for this data item.
            experience: Normalized experience config. It may include target
                skill selectors, explicit version names, supersedes values,
                storage refs, publish flags, and entity/spec metadata.
            job_config: Merged job config used for default generated version
                names and policy.
            metadata: Submit metadata copied into generated specs.

        Returns:
            Summary containing previous/new harness refs, previous/new skill
            refs, normalized experience record, and raw create/publish payloads.
        """

        harness_payload = self.get_harness_version(harness_ref.key, harness_ref.version)
        harness_spec = deepcopy(dict(harness_payload.get("spec") or {}))
        if not harness_spec:
            raise EnvArenaError(f"Harness {harness_ref.key}@{harness_ref.version} has no spec.")

        old_skill_ref = self._select_harness_experience_skill_ref(
            harness_ref=harness_ref,
            harness_spec=harness_spec,
            experience=experience,
        )
        experience_record = self._experience_record_for_data_id(data_id, experience)
        if old_skill_ref is None:
            return self._create_new_stream_data_experience_skill_and_harness(
                stream_id=stream_id,
                data_id=data_id,
                harness_ref=harness_ref,
                harness_spec=harness_spec,
                experience=experience,
                experience_record=experience_record,
                job_config=job_config,
                metadata=metadata,
            )

        skill_payload = self.get_skill_version(old_skill_ref.key, old_skill_ref.version)
        skill_spec = deepcopy(dict(skill_payload.get("spec") or {}))
        if not skill_spec:
            raise EnvArenaError(f"Skill {old_skill_ref.key}@{old_skill_ref.version} has no spec.")

        new_skill_ref = EnvArenaVersionRef(
            key=old_skill_ref.key,
            version=self._experience_update_version(
                experience,
                job_config,
                old_version=old_skill_ref.version,
                field_names=("version", "skill_version", "new_skill_version"),
                config_section="experience_skill",
            ),
        )
        updated_skill_spec = self._skill_spec_with_experience(
            base_spec=skill_spec,
            data_id=data_id,
            experience=experience,
            experience_record=experience_record,
            old_skill_ref=old_skill_ref,
            new_skill_ref=new_skill_ref,
            stream_id=stream_id,
            harness_ref=harness_ref,
            metadata=metadata,
        )
        skill_version_payload = self.create_skill_version(
            old_skill_ref.key,
            version=new_skill_ref.version,
            spec=updated_skill_spec,
            supersedes=str(experience.get("skill_supersedes") or experience.get("supersedes") or old_skill_ref.version),
            storage_ref=experience.get("skill_storage_ref", experience.get("storage_ref")),
        )
        skill_publish_payload = (
            self.publish_skill_version(new_skill_ref.key, new_skill_ref.version)
            if bool(experience.get("publish_skill", experience.get("publish", True)))
            else None
        )

        new_harness_ref = EnvArenaVersionRef(
            key=harness_ref.key,
            version=self._experience_update_version(
                experience,
                job_config,
                old_version=harness_ref.version,
                field_names=("harness_version", "new_harness_version"),
                config_section="experience_harness",
            ),
        )
        updated_harness_spec = self._harness_spec_with_replaced_skill(
            harness_spec=harness_spec,
            old_harness_ref=harness_ref,
            new_harness_ref=new_harness_ref,
            old_skill_ref=old_skill_ref,
            new_skill_ref=new_skill_ref,
            stream_id=stream_id,
            data_id=data_id,
            metadata=metadata,
        )
        harness_version_payload = self.create_harness_version(
            harness_ref.key,
            version=new_harness_ref.version,
            spec=updated_harness_spec,
            supersedes=str(experience.get("harness_supersedes") or harness_ref.version),
            storage_ref=experience.get("harness_storage_ref"),
        )
        harness_publish_payload = (
            self.publish_harness_version(new_harness_ref.key, new_harness_ref.version)
            if bool(experience.get("publish_harness", experience.get("publish", True)))
            else None
        )
        return {
            "stream_id": stream_id,
            "data_id": data_id,
            "previous_harness_ref": harness_ref.to_dict(),
            "harness_ref": new_harness_ref.to_dict(),
            "previous_skill_ref": old_skill_ref.to_dict(),
            "skill_ref": new_skill_ref.to_dict(),
            "experience_record": experience_record,
            "skill_version": skill_version_payload,
            "skill_publish": skill_publish_payload,
            "harness_version": harness_version_payload,
            "harness_publish": harness_publish_payload,
        }

    def _create_new_stream_data_experience_skill_and_harness(
        self,
        *,
        stream_id: str,
        data_id: str,
        harness_ref: EnvArenaVersionRef,
        harness_spec: Mapping[str, Any],
        experience: Mapping[str, Any],
        experience_record: Mapping[str, Any],
        job_config: Mapping[str, Any],
        metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Create a new experience skill and harness when none exists.

        Parameters:
            stream_id: Source stream ID used in generated metadata.
            data_id: Stream data ID receiving the experience.
            harness_ref: Base harness version used as the template.
            harness_spec: Spec of the base harness.
            experience: Normalized experience config with optional key,
                version, name, description, tags, publish, and storage fields.
            experience_record: Structured per-task experience record to embed
                into the generated skill spec.
            job_config: Merged job config used for generated key/version
                defaults.
            metadata: Submit metadata copied to generated assets.

        Returns:
            Summary containing created skill and harness refs plus raw
            create/publish payloads.
        """

        new_skill_ref = self._new_experience_skill_ref(
            stream_id=stream_id,
            data_id=data_id,
            experience=experience,
            job_config=job_config,
        )
        skill_name = str(
            experience.get("skill_name")
            or experience.get("name")
            or f"AReaL-RSI experience skill for {data_id}"
        )
        skill_description = str(
            experience.get("skill_description")
            or experience.get("description")
            or f"Task-specific experience generated for stream data {data_id}."
        )
        skill_entity = self.create_skill(
            key=new_skill_ref.key,
            name=skill_name,
            description=skill_description,
            tags=list(experience.get("skill_tags", ["areal-rsi", "experience-update"])),
            metadata={
                **dict(metadata),
                **(
                    dict(experience.get("skill_entity_metadata", {}))
                    if isinstance(experience.get("skill_entity_metadata"), Mapping)
                    else {}
                ),
                "generated_by": "areal-rsi",
                "source_stream_id": stream_id,
                "data_id": data_id,
                "base_harness_ref": f"{harness_ref.key}@{harness_ref.version}",
            },
        )
        updated_skill_spec = self._skill_spec_with_experience(
            base_spec=self._new_experience_skill_base_spec(
                name=skill_name,
                description=skill_description,
                experience=experience,
            ),
            data_id=data_id,
            experience=experience,
            experience_record=experience_record,
            old_skill_ref=None,
            new_skill_ref=new_skill_ref,
            stream_id=stream_id,
            harness_ref=harness_ref,
            metadata=metadata,
        )
        skill_version_payload = self.create_skill_version(
            new_skill_ref.key,
            version=new_skill_ref.version,
            spec=updated_skill_spec,
            supersedes=experience.get("skill_supersedes", experience.get("supersedes")),
            storage_ref=experience.get("skill_storage_ref", experience.get("storage_ref")),
        )
        skill_publish_payload = (
            self.publish_skill_version(new_skill_ref.key, new_skill_ref.version)
            if bool(experience.get("publish_skill", experience.get("publish", True)))
            else None
        )

        new_harness_ref = self._new_experience_harness_ref(
            base_harness_ref=harness_ref,
            data_id=data_id,
            experience=experience,
            job_config=job_config,
        )
        harness_name = str(
            experience.get("harness_name")
            or f"AReaL-RSI experience harness for {data_id}"
        )
        harness_description = str(
            experience.get("harness_description")
            or f"AReaL-RSI harness derived from {harness_ref.key}@{harness_ref.version}."
        )
        harness_entity = self.create_harness(
            key=new_harness_ref.key,
            name=harness_name,
            description=harness_description,
            tags=list(experience.get("harness_tags", ["areal-rsi", "experience-update"])),
            metadata={
                **dict(metadata),
                **(
                    dict(experience.get("harness_entity_metadata", {}))
                    if isinstance(experience.get("harness_entity_metadata"), Mapping)
                    else {}
                ),
                "generated_by": "areal-rsi",
                "source_stream_id": stream_id,
                "data_id": data_id,
                "base_harness_ref": f"{harness_ref.key}@{harness_ref.version}",
                "skill_ref": f"{new_skill_ref.key}@{new_skill_ref.version}",
            },
        )
        updated_harness_spec = self._harness_spec_with_added_skill(
            harness_spec=harness_spec,
            old_harness_ref=harness_ref,
            new_harness_ref=new_harness_ref,
            new_skill_ref=new_skill_ref,
            stream_id=stream_id,
            data_id=data_id,
            metadata=metadata,
        )
        harness_version_payload = self.create_harness_version(
            new_harness_ref.key,
            version=new_harness_ref.version,
            spec=updated_harness_spec,
            supersedes=experience.get("harness_supersedes"),
            storage_ref=experience.get("harness_storage_ref"),
        )
        harness_publish_payload = (
            self.publish_harness_version(new_harness_ref.key, new_harness_ref.version)
            if bool(experience.get("publish_harness", experience.get("publish", True)))
            else None
        )
        return {
            "stream_id": stream_id,
            "data_id": data_id,
            "previous_harness_ref": harness_ref.to_dict(),
            "harness_ref": new_harness_ref.to_dict(),
            "previous_skill_ref": None,
            "skill_ref": new_skill_ref.to_dict(),
            "experience_record": dict(experience_record),
            "skill_entity": skill_entity,
            "skill_version": skill_version_payload,
            "skill_publish": skill_publish_payload,
            "harness_entity": harness_entity,
            "harness_version": harness_version_payload,
            "harness_publish": harness_publish_payload,
        }

    def _select_harness_experience_skill_ref(
        self,
        *,
        harness_ref: EnvArenaVersionRef,
        harness_spec: Mapping[str, Any],
        experience: Mapping[str, Any],
    ) -> EnvArenaVersionRef | None:
        """Choose the existing harness skill that should receive experience.

        Parameters:
            harness_ref: Current harness registry ref, used for error messages.
            harness_spec: Harness spec containing optional ``skill_refs``.
            experience: Experience config. It may select a skill with
                ``skill_ref``/``target_skill_ref`` or
                ``skill_key``/``target_skill_key``.

        Returns:
            Selected skill ref, ``None`` when the harness has no skill refs, or
            raises if multiple skills exist and no unambiguous target is given.
        """

        raw_skill_refs = harness_spec.get("skill_refs", [])
        if raw_skill_refs is None:
            raw_skill_refs = []
        if isinstance(raw_skill_refs, str) or not isinstance(raw_skill_refs, Sequence):
            raise EnvArenaError(f"Harness {harness_ref.key}@{harness_ref.version} has invalid skill_refs.")
        skill_refs = [self._as_version_ref(ref) for ref in raw_skill_refs]
        if not skill_refs:
            return None

        target_ref = self._experience_target_skill_ref(experience)
        if target_ref is not None:
            for skill_ref in skill_refs:
                if skill_ref == target_ref:
                    return skill_ref
            return None

        target_key = self._experience_target_skill_key(experience)
        if target_key:
            matches = [skill_ref for skill_ref in skill_refs if skill_ref.key == target_key]
            if len(matches) == 1:
                return matches[0]
            if not matches:
                return None
            raise EnvArenaConfigurationError(
                f"Harness {harness_ref.key}@{harness_ref.version} contains multiple versions of skill "
                f"{target_key!r}; use skill_ref to select one."
            )

        if len(skill_refs) == 1:
            return skill_refs[0]
        raise EnvArenaConfigurationError(
            f"Harness {harness_ref.key}@{harness_ref.version} has multiple skill_refs; "
            "stream experience update requires skill_key or skill_ref."
        )

    def _experience_target_skill_ref(self, experience: Mapping[str, Any]) -> EnvArenaVersionRef | None:
        """Read an explicit target skill ref from experience config.

        Parameters:
            experience: Experience config that may contain ``skill_ref`` or
                ``target_skill_ref``.

        Returns:
            Parsed skill ref, or ``None`` when no explicit ref is set.
        """

        for field_name in ("skill_ref", "target_skill_ref"):
            if experience.get(field_name) is not None:
                return self._as_version_ref(experience[field_name])
        return None

    @staticmethod
    def _experience_target_skill_key(experience: Mapping[str, Any]) -> str:
        """Read an explicit target skill key from experience config.

        Parameters:
            experience: Experience config that may contain ``skill_key`` or
                ``target_skill_key``.

        Returns:
            Non-empty skill key, or an empty string when absent.
        """

        for field_name in ("skill_key", "target_skill_key"):
            value = str(experience.get(field_name) or "").strip()
            if value:
                return value
        return ""

    def _new_experience_skill_ref(
        self,
        *,
        stream_id: str,
        data_id: str,
        experience: Mapping[str, Any],
        job_config: Mapping[str, Any],
    ) -> EnvArenaVersionRef:
        """Build the new skill ref for a generated experience skill.

        Parameters:
            stream_id: Source stream ID used in derived-key seed.
            data_id: Stream data ID used in derived-key seed.
            experience: Experience config. It may provide explicit
                ``skill_ref``, ``skill_key``, ``target_skill_key``,
                ``new_skill_key``, ``version``, ``skill_version``, or
                ``new_skill_version``.
            job_config: Merged job config. ``experience_skill`` may provide
                default ``key``/``version`` values.

        Returns:
            New skill registry ref.
        """

        target_ref = self._experience_target_skill_ref(experience)
        key = (
            str(target_ref.key)
            if target_ref is not None
            else self._new_experience_asset_key(
                experience,
                job_config,
                field_names=("skill_key", "target_skill_key", "new_skill_key"),
                config_section="experience_skill",
                seed=f"{stream_id}-{data_id}-experience-skill",
            )
        )
        version = self._new_experience_asset_version(
            experience,
            job_config,
            field_names=("version", "skill_version", "new_skill_version"),
            config_section="experience_skill",
            default=str(target_ref.version) if target_ref is not None else "1.0.0",
        )
        return EnvArenaVersionRef(key=key, version=version)

    def _new_experience_harness_ref(
        self,
        *,
        base_harness_ref: EnvArenaVersionRef,
        data_id: str,
        experience: Mapping[str, Any],
        job_config: Mapping[str, Any],
    ) -> EnvArenaVersionRef:
        """Build the new harness ref for a generated experience harness.

        Parameters:
            base_harness_ref: Harness ref used as the template and derived-key
                seed.
            data_id: Stream data ID used in derived-key seed.
            experience: Experience config. It may provide ``harness_key``,
                ``target_harness_key``, ``new_harness_key``,
                ``harness_version``, or ``new_harness_version``.
            job_config: Merged job config. ``experience_harness`` may provide
                default ``key``/``version`` values.

        Returns:
            New harness registry ref.
        """

        key = self._new_experience_asset_key(
            experience,
            job_config,
            field_names=("harness_key", "target_harness_key", "new_harness_key"),
            config_section="experience_harness",
            seed=f"{base_harness_ref.key}-{data_id}-experience-harness",
        )
        version = self._new_experience_asset_version(
            experience,
            job_config,
            field_names=("harness_version", "new_harness_version"),
            config_section="experience_harness",
            default="1.0.0",
        )
        return EnvArenaVersionRef(key=key, version=version)

    def _new_experience_asset_key(
        self,
        experience: Mapping[str, Any],
        job_config: Mapping[str, Any],
        *,
        field_names: Sequence[str],
        config_section: str,
        seed: str,
    ) -> str:
        """Resolve a generated experience asset key.

        Parameters:
            experience: Experience config checked first for explicit key fields.
            job_config: Merged job config checked second for ``config_section``.
            field_names: Experience/job-config field names to inspect.
            config_section: Job config section name, such as
                ``experience_skill`` or ``experience_harness``.
            seed: Fallback seed used to derive a unique Arena key.

        Returns:
            Explicit key when provided, otherwise a derived asset key.
        """

        for field_name in field_names:
            value = str(experience.get(field_name) or "").strip()
            if value:
                return value
        section = job_config.get(config_section)
        if isinstance(section, Mapping):
            for field_name in ("key", *field_names):
                value = str(section.get(field_name) or "").strip()
                if value:
                    return value
        return self._derived_asset_key(seed)

    @staticmethod
    def _new_experience_asset_version(
        experience: Mapping[str, Any],
        job_config: Mapping[str, Any],
        *,
        field_names: Sequence[str],
        config_section: str,
        default: str,
    ) -> str:
        """Resolve a generated experience asset version.

        Parameters:
            experience: Experience config checked first for explicit version
                fields.
            job_config: Merged job config checked second for ``config_section``.
            field_names: Experience/job-config version field names to inspect.
            config_section: Job config section name, such as
                ``experience_skill`` or ``experience_harness``.
            default: Version returned when no explicit value is found.

        Returns:
            Resolved version string.
        """

        for field_name in field_names:
            value = str(experience.get(field_name) or "").strip()
            if value:
                return value
        section = job_config.get(config_section)
        if isinstance(section, Mapping):
            for field_name in ("version", *field_names):
                value = str(section.get(field_name) or "").strip()
                if value:
                    return value
        return default

    @staticmethod
    def _new_experience_skill_base_spec(
        *,
        name: str,
        description: str,
        experience: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Create the base spec for a new inline experience skill.

        Parameters:
            name: Skill display name used in the manifest when absent from
                ``experience.spec``.
            description: Skill description used in the manifest when absent.
            experience: Experience config. Optional fields include ``spec``,
                ``bundle_uri``, ``bundle_media_type``, ``manifest_metadata``,
                ``source_uri``, and ``source_kind``.

        Returns:
            Skill spec with ``bundle`` and ``manifest`` populated.
        """

        spec = deepcopy(dict(experience.get("spec") or {})) if isinstance(experience.get("spec"), Mapping) else {}
        bundle = dict(spec.get("bundle", {})) if isinstance(spec.get("bundle"), Mapping) else {}
        if not bundle:
            bundle = {
                "uri": str(experience.get("bundle_uri") or "inline://areal-rsi/experience-skill"),
                "media_type": str(
                    experience.get("bundle_media_type") or "application/vnd.areal.skill.experience+json"
                ),
            }
        spec["bundle"] = bundle

        manifest = dict(spec.get("manifest", {})) if isinstance(spec.get("manifest"), Mapping) else {}
        manifest.setdefault("name", name)
        manifest.setdefault("description", description)
        if isinstance(experience.get("manifest_metadata"), Mapping):
            manifest_metadata = dict(manifest.get("metadata", {})) if isinstance(manifest.get("metadata"), Mapping) else {}
            manifest_metadata.update(dict(experience["manifest_metadata"]))
            manifest["metadata"] = manifest_metadata
        spec["manifest"] = manifest
        if experience.get("source_uri") is not None and "source" not in spec:
            spec["source"] = {
                "kind": str(experience.get("source_kind") or "url"),
                "uri": str(experience["source_uri"]),
            }
        return spec

    def _experience_update_version(
        self,
        experience: Mapping[str, Any],
        job_config: Mapping[str, Any],
        *,
        old_version: str,
        field_names: Sequence[str],
        config_section: str,
    ) -> str:
        """Resolve the next version for updating an existing asset.

        Parameters:
            experience: Experience config checked first for explicit version
                fields.
            job_config: Merged job config checked second for ``config_section``.
            old_version: Current version used as the fallback prefix.
            field_names: Version field names to inspect.
            config_section: Job config section name, such as
                ``experience_skill`` or ``experience_harness``.

        Returns:
            Explicit version when provided, otherwise ``old_version`` suffixed
            with an RSI-generated identifier.
        """

        for field_name in field_names:
            value = str(experience.get(field_name) or "").strip()
            if value:
                return value
        section = job_config.get(config_section)
        if isinstance(section, Mapping):
            for field_name in ("version", *field_names):
                value = str(section.get(field_name) or "").strip()
                if value:
                    return value
        return f"{old_version}-rsi-{uuid4().hex[:8]}"

    def _experience_record_for_data_id(
        self,
        data_id: str,
        experience: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Build the structured task-experience record for one data ID.

        Parameters:
            data_id: Stream data ID used as the canonical ``task_id``.
            experience: Experience config. Supported forms include nested
                ``experience_record``, ``task_experience``, mapping-shaped
                ``experience``, or text fields ``experience``, ``content``, and
                ``text``. Optional metadata fields include ``source_traj_ids``,
                ``analysis``, ``training_recommendation``, ``metadata``,
                ``status``, and ``task_id``.

        Returns:
            Normalized task-experience record with schema version, canonical
            task ID, status, experience text, and optional metadata.
        """

        if isinstance(experience.get("experience_record"), Mapping):
            record = dict(experience["experience_record"])
        elif isinstance(experience.get("task_experience"), Mapping):
            record = dict(experience["task_experience"])
        elif isinstance(experience.get("experience"), Mapping):
            record = dict(experience["experience"])
        else:
            record = {}

        if "experience" not in record:
            for field_name in ("experience", "content", "text"):
                value = experience.get(field_name)
                if isinstance(value, str) and value.strip():
                    record["experience"] = value.strip()
                    break
        if not isinstance(record.get("experience"), str) or not str(record["experience"]).strip():
            raise EnvArenaConfigurationError(f"stream data_id {data_id!r} experience text must be non-empty.")
        record["experience"] = str(record["experience"]).strip()

        original_task_id = str(record.get("task_id") or experience.get("task_id") or "").strip()
        record["schema_version"] = str(record.get("schema_version") or "task-experience.v1")
        record["task_id"] = data_id
        record["status"] = str(record.get("status") or experience.get("status") or "completed")
        for field_name in ("source_traj_ids", "analysis", "training_recommendation"):
            if field_name not in record and experience.get(field_name) is not None:
                value = experience[field_name]
                record[field_name] = (
                    list(value)
                    if isinstance(value, Sequence) and not isinstance(value, str)
                    else value
                )

        record_metadata = dict(record.get("metadata", {})) if isinstance(record.get("metadata"), Mapping) else {}
        if isinstance(experience.get("metadata"), Mapping):
            record_metadata.update(dict(experience["metadata"]))
        if original_task_id and original_task_id != data_id:
            record_metadata.setdefault("source_task_id", original_task_id)
        if record_metadata:
            record["metadata"] = record_metadata
        return record

    def _skill_spec_with_experience(
        self,
        *,
        base_spec: Mapping[str, Any],
        data_id: str,
        experience: Mapping[str, Any],
        experience_record: Mapping[str, Any],
        old_skill_ref: EnvArenaVersionRef | None,
        new_skill_ref: EnvArenaVersionRef,
        stream_id: str,
        harness_ref: EnvArenaVersionRef,
        metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Merge a task-experience record into a skill spec.

        Parameters:
            base_spec: Existing or generated skill spec.
            data_id: Stream data ID whose prior record should be replaced.
            experience: Experience config. ``spec`` is merged over
                ``base_spec`` and ``skill_metadata`` is merged into spec
                metadata.
            experience_record: Normalized record produced by
                ``_experience_record_for_data_id``.
            old_skill_ref: Previous skill ref when updating an existing skill,
                or ``None`` for newly created skills.
            new_skill_ref: New skill version ref.
            stream_id: Source stream ID for metadata.
            harness_ref: Harness ref that uses this skill.
            metadata: Submit metadata copied into the spec metadata.

        Returns:
            Updated skill spec with ``task_experiences`` and injection defaults.
        """

        spec = deepcopy(dict(base_spec))
        if isinstance(experience.get("spec"), Mapping):
            spec = self._merge_job_config(spec, experience["spec"])

        raw_records = spec.get("task_experiences", [])
        records = (
            [dict(record) for record in raw_records if isinstance(record, Mapping)]
            if isinstance(raw_records, list)
            else []
        )
        records = [record for record in records if str(record.get("task_id") or "") != data_id]
        records.append(dict(experience_record))
        spec["task_experiences"] = records
        spec.setdefault("schema_version", "rsi-experience-skill.v1")
        injection = dict(spec.get("injection", {})) if isinstance(spec.get("injection"), Mapping) else {}
        injection.setdefault("target_role", "user")
        injection.setdefault("position", "append")
        injection.setdefault("selection_key", "task_id")
        spec["injection"] = injection

        spec_metadata = dict(spec.get("metadata", {})) if isinstance(spec.get("metadata"), Mapping) else {}
        spec_metadata.update(
            {
                **dict(metadata),
                "generated_by": "areal-rsi",
                "source_stream_id": stream_id,
                "data_id": data_id,
                **(
                    {"previous_skill_ref": f"{old_skill_ref.key}@{old_skill_ref.version}"}
                    if old_skill_ref is not None
                    else {}
                ),
                "skill_ref": f"{new_skill_ref.key}@{new_skill_ref.version}",
                "harness_ref": f"{harness_ref.key}@{harness_ref.version}",
            }
        )
        if isinstance(experience.get("skill_metadata"), Mapping):
            spec_metadata.update(dict(experience["skill_metadata"]))
        spec["metadata"] = self._string_metadata(spec_metadata)
        return spec

    def _harness_spec_with_added_skill(
        self,
        *,
        harness_spec: Mapping[str, Any],
        old_harness_ref: EnvArenaVersionRef,
        new_harness_ref: EnvArenaVersionRef,
        new_skill_ref: EnvArenaVersionRef,
        stream_id: str,
        data_id: str,
        metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Add a generated skill ref to a derived harness spec.

        Parameters:
            harness_spec: Base harness spec.
            old_harness_ref: Source harness ref used for metadata and
                validation.
            new_harness_ref: New harness ref written into metadata.
            new_skill_ref: Skill ref to append to ``skill_refs``.
            stream_id: Source stream ID for metadata.
            data_id: Stream data ID for metadata.
            metadata: Submit metadata copied into harness spec metadata.

        Returns:
            Harness spec with deduplicated ``skill_refs`` plus the new skill.
        """

        spec = deepcopy(dict(harness_spec))
        raw_skill_refs = spec.get("skill_refs", [])
        if raw_skill_refs is None:
            raw_skill_refs = []
        if isinstance(raw_skill_refs, str) or not isinstance(raw_skill_refs, Sequence):
            raise EnvArenaError(
                f"Harness {old_harness_ref.key}@{old_harness_ref.version} has invalid skill_refs."
            )

        updated_skill_refs: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for raw_ref in raw_skill_refs:
            skill_ref = self._as_version_ref(raw_ref)
            key = (skill_ref.key, skill_ref.version)
            if key in seen:
                continue
            updated_skill_refs.append(skill_ref.to_dict())
            seen.add(key)
        new_key = (new_skill_ref.key, new_skill_ref.version)
        if new_key not in seen:
            updated_skill_refs.append(new_skill_ref.to_dict())
        spec["skill_refs"] = updated_skill_refs

        spec_metadata = dict(spec.get("metadata", {})) if isinstance(spec.get("metadata"), Mapping) else {}
        spec_metadata.update(
            {
                **dict(metadata),
                "generated_by": "areal-rsi",
                "source_stream_id": stream_id,
                "data_id": data_id,
                "previous_harness_ref": f"{old_harness_ref.key}@{old_harness_ref.version}",
                "harness_ref": f"{new_harness_ref.key}@{new_harness_ref.version}",
                "skill_ref": f"{new_skill_ref.key}@{new_skill_ref.version}",
            }
        )
        spec["metadata"] = self._string_metadata(spec_metadata)
        return spec

    def _harness_spec_with_replaced_skill(
        self,
        *,
        harness_spec: Mapping[str, Any],
        old_harness_ref: EnvArenaVersionRef,
        new_harness_ref: EnvArenaVersionRef,
        old_skill_ref: EnvArenaVersionRef,
        new_skill_ref: EnvArenaVersionRef,
        stream_id: str,
        data_id: str,
        metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Replace one existing skill ref in a harness spec.

        Parameters:
            harness_spec: Existing harness spec.
            old_harness_ref: Source harness ref used for metadata and errors.
            new_harness_ref: New harness ref written into metadata.
            old_skill_ref: Skill ref to replace.
            new_skill_ref: Replacement skill ref.
            stream_id: Source stream ID for metadata.
            data_id: Stream data ID for metadata.
            metadata: Submit metadata copied into harness spec metadata.

        Returns:
            Harness spec with ``old_skill_ref`` replaced by ``new_skill_ref``.
        """

        spec = deepcopy(dict(harness_spec))
        replaced = False
        updated_skill_refs: list[dict[str, str]] = []
        for raw_ref in self._as_sequence(spec.get("skill_refs", [])):
            skill_ref = self._as_version_ref(raw_ref)
            if skill_ref == old_skill_ref and not replaced:
                updated_skill_refs.append(new_skill_ref.to_dict())
                replaced = True
            else:
                updated_skill_refs.append(skill_ref.to_dict())
        if not replaced:
            raise EnvArenaError(
                f"Harness {old_harness_ref.key}@{old_harness_ref.version} did not contain selected skill "
                f"{old_skill_ref.key}@{old_skill_ref.version}."
            )
        spec["skill_refs"] = updated_skill_refs

        spec_metadata = dict(spec.get("metadata", {})) if isinstance(spec.get("metadata"), Mapping) else {}
        spec_metadata.update(
            {
                **dict(metadata),
                "generated_by": "areal-rsi",
                "source_stream_id": stream_id,
                "data_id": data_id,
                "previous_harness_ref": f"{old_harness_ref.key}@{old_harness_ref.version}",
                "harness_ref": f"{new_harness_ref.key}@{new_harness_ref.version}",
                "previous_skill_ref": f"{old_skill_ref.key}@{old_skill_ref.version}",
                "skill_ref": f"{new_skill_ref.key}@{new_skill_ref.version}",
            }
        )
        spec["metadata"] = self._string_metadata(spec_metadata)
        return spec

    def _resolve_stream_skill_bindings(
        self,
        skills: Sequence[Mapping[str, Any]],
        *,
        data_ids: Sequence[str],
    ) -> tuple[dict[str, dict[str, Any]], dict[str, list[EnvArenaVersionRef]]]:
        """Resolve JSON-list-style skill bindings into per-data maps.

        Parameters:
            skills: Sequence of mappings. Each item must contain ``data_id``.
                ``type`` may be ``"experience"`` or ``"ref"``.
                ``type="experience"`` uses ``content`` for experience
                text/config. ``type="ref"`` uses ``ref`` for an existing skill
                registry ref. Each selected data ID may appear at most once.
            data_ids: Selected data IDs. Skill bindings must target these IDs.

        Returns:
            Tuple of ``(experiences_by_data_id, skill_refs_by_data_id)``.
            Experience items create skill/harness versions; ref items create
            task-level harness overrides.
        """

        selected = {str(data_id) for data_id in data_ids}
        experiences_by_data_id: dict[str, dict[str, Any]] = {}
        refs_by_data_id: dict[str, list[EnvArenaVersionRef]] = {}
        for skill in skills:
            if not isinstance(skill, Mapping):
                raise EnvArenaConfigurationError(
                    "skills items must be mappings with data_id, type, and content for experience or ref for ref."
                )
            data_id = str(skill.get("data_id") or "").strip()
            if not data_id:
                raise EnvArenaConfigurationError("skills items require non-empty data_id.")
            if data_id not in selected:
                raise EnvArenaConfigurationError(f"skill binding for data_id {data_id!r} is not selected.")
            if data_id in experiences_by_data_id or data_id in refs_by_data_id:
                raise EnvArenaConfigurationError(f"data_id {data_id!r} has multiple skill bindings.")
            kind = self._stream_skill_item_type(skill)
            if kind == "experience":
                experiences_by_data_id[data_id] = self._stream_skill_experience_config(skill)
                continue
            refs_by_data_id[data_id] = [
                self._resolve_registry_ref("skills", self._stream_skill_ref_payload(skill), "skill")
            ]

        if skills:
            covered = set(experiences_by_data_id) | set(refs_by_data_id)
            missing = [data_id for data_id in data_ids if data_id not in covered]
            if missing:
                raise EnvArenaConfigurationError(
                    "skills are missing bindings for selected data_ids: " + ", ".join(missing)
                )
        return experiences_by_data_id, refs_by_data_id

    def _stream_skill_item_type(self, skill: Mapping[str, Any]) -> str:
        """Resolve one skill binding item type.

        Parameters:
            skill: Mapping with optional ``type`` plus ``content`` or ``ref``.

        Returns:
            ``"experience"`` or ``"ref"``.
        """

        raw_type = str(skill.get("type") or "").strip().lower()
        if raw_type in {"experience", "ref"}:
            return raw_type
        if raw_type:
            raise EnvArenaConfigurationError("skills item type must be 'experience' or 'ref'.")
        if skill.get("ref") is not None:
            return "ref"
        return "experience"

    @staticmethod
    def _stream_skill_experience_config(skill: Mapping[str, Any]) -> dict[str, Any]:
        """Return normalized experience config from one skill binding item.

        Parameters:
            skill: Mapping with ``content`` as text/config and optional extra
                fields such as version overrides or metadata.

        Returns:
            Experience config mapping suitable for skill/harness generation.
        """

        if "content" not in skill:
            raise EnvArenaConfigurationError("experience skill bindings require content.")
        excluded = {"data_id", "type", "content", "ref"}
        extras = {key: value for key, value in skill.items() if key not in excluded}
        content = skill["content"]
        if isinstance(content, Mapping):
            return {**dict(content), **extras}
        experience = EnvArenaClient._stream_experience_config(content)
        experience.update(extras)
        return experience

    @staticmethod
    def _stream_skill_ref_payload(skill: Mapping[str, Any]) -> Any:
        """Return the skill ref payload from one ref binding item.

        Parameters:
            skill: Mapping with ``ref``.

        Returns:
            Raw ref payload accepted by the skill registry ref parser.
        """

        if skill.get("ref") is not None:
            return skill["ref"]
        raise EnvArenaConfigurationError("ref skill bindings require ref.")

    @staticmethod
    def _as_sequence(value: Any) -> list[Any]:
        """Normalize a scalar or sequence-like value into a list.

        Parameters:
            value: Any value. Strings are treated as scalar values instead of
                character sequences.

        Returns:
            ``list(value)`` for non-string sequences; otherwise ``[value]``.
        """

        if isinstance(value, str) or not isinstance(value, Sequence):
            return [value]
        return list(value)

    @staticmethod
    def _dedupe_refs(refs: Sequence[EnvArenaVersionRef]) -> list[EnvArenaVersionRef]:
        """Deduplicate registry refs while preserving encounter order.

        Parameters:
            refs: Sequence of registry version refs.

        Returns:
            List with duplicate ``(key, version)`` pairs removed.
        """

        deduped: list[EnvArenaVersionRef] = []
        seen: set[tuple[str, str]] = set()
        for ref in refs:
            key = (ref.key, ref.version)
            if key in seen:
                continue
            deduped.append(ref)
            seen.add(key)
        return deduped

    def _create_stream_benchmark(
        self,
        *,
        stream_id: str,
        data_items: Sequence[Mapping[str, Any]],
        reward_ref: EnvArenaVersionRef,
        harness_ref: EnvArenaVersionRef,
        skill_refs: Sequence[EnvArenaVersionRef],
        fork_stream_id: str | None,
        job_config: Mapping[str, Any],
        request_metadata: Mapping[str, Any],
    ) -> EnvArenaVersionRef:
        """Create a benchmark version from selected stream data items.

        Parameters:
            stream_id: Source stream ID used in benchmark metadata.
            data_items: Selected dataset items. Each item should contain
                ``data_id`` and optionally ``env_ref``.
            reward_ref: Default reward ref for the benchmark.
            harness_ref: Default harness ref for the benchmark.
            skill_refs: Skill refs associated with generated harness behavior.
            fork_stream_id: Optional legacy fork stream ID copied into metadata.
            job_config: Merged job config controlling key/version, tags,
                aggregation, and descriptions.
            request_metadata: Submit metadata copied into benchmark metadata.

        Returns:
            Created and published benchmark version ref.
        """

        key = str(job_config.get("benchmark_key") or self._derived_asset_key(f"{stream_id}-benchmark"))
        version = str(job_config.get("benchmark_version") or "1.0.0")
        env_refs: list[dict[str, Any]] = []
        for item in data_items:
            data_id = str(item.get("data_id", ""))
            env_ref = self._env_ref_for_stream_item(item)
            params: dict[str, Any] = {
                "data_id": data_id,
                "source_stream_id": stream_id,
                "harness_ref": harness_ref.to_dict(),
            }
            if fork_stream_id:
                params["fork_stream_id"] = fork_stream_id
            if skill_refs:
                params["skill_refs"] = [ref.to_dict() for ref in skill_refs]
            env_refs.append({"env_ref": env_ref.to_dict(), "params": params})
        metadata = {
            **dict(request_metadata),
            "source_stream_id": stream_id,
            "harness_ref": harness_ref.to_dict(),
            "generated_by": "areal-rsi",
            **({"fork_stream_id": fork_stream_id} if fork_stream_id else {}),
        }
        spec = {
            "reward_ref": reward_ref.to_dict(),
            "env_refs": env_refs,
            "aggregation": dict(job_config.get("aggregation", {"method": "mean", "failed_task_policy": "failed_as_zero"})),
            "tags": list(job_config.get("benchmark_tags", job_config.get("tags", []))),
            "metadata": metadata,
        }
        self.create_benchmark(
            key=key,
            name=str(job_config.get("benchmark_name") or key),
            description=str(job_config.get("benchmark_description") or f"AReaL-RSI stream benchmark for {stream_id}"),
            tags=list(job_config.get("benchmark_entity_tags", ["areal-rsi", "stream-eval"])),
            metadata={
                "source_stream_id": stream_id,
                **({"fork_stream_id": fork_stream_id} if fork_stream_id else {}),
            },
        )
        self.create_benchmark_version(key, version=version, spec=spec)
        self.publish_benchmark_version(key, version)
        return EnvArenaVersionRef(key=key, version=version)

    def _env_ref_for_stream_item(self, item: Mapping[str, Any]) -> EnvArenaVersionRef:
        """Resolve the environment ref represented by a stream dataset item.

        Parameters:
            item: Stream dataset item. Preferred field is ``env_ref``. When it
                is absent, ``data_id`` is used after stripping an ``env:``
                prefix.

        Returns:
            Environment registry version ref.
        """

        env_ref = item.get("env_ref")
        if env_ref is not None:
            return self._as_version_ref(env_ref)
        data_id = str(item.get("data_id", "")).strip()
        if data_id.startswith("env:"):
            data_id = data_id.removeprefix("env:")
        return self._as_version_ref(data_id)

    def _create_fork_harness(
        self,
        base_harness_ref: EnvArenaVersionRef,
        skill_refs: Sequence[EnvArenaVersionRef],
        source_stream_id: str,
        job_config: Mapping[str, Any],
        *,
        key_seed: str | None = None,
        extra_metadata: Mapping[str, Any] | None = None,
    ) -> EnvArenaVersionRef:
        """Create a derived harness version with additional skill refs.

        This helper keeps its historical name, but the current stream submit
        flow uses it to create task-level or batch-level harness overrides
        without creating a fork stream.

        Parameters:
            base_harness_ref: Existing harness version used as the spec
                template.
            skill_refs: Skill refs to merge into the base harness spec.
            source_stream_id: Source stream ID recorded in generated metadata.
            job_config: Merged job config. Supported fields include
                ``replace_skill_refs``, ``harness_key``, ``harness_version``,
                ``harness_name``, ``harness_description``, and
                ``harness_entity_tags``.
            key_seed: Optional seed for derived harness key generation. When
                absent, ``job_config.harness_key`` may be used.
            extra_metadata: Optional metadata merged into the derived harness
                spec metadata, for example the task ``data_id``.

        Returns:
            Created and published harness version ref.
        """

        version_payload = self.get_harness_version(base_harness_ref.key, base_harness_ref.version)
        base_spec = dict(version_payload.get("spec", {}))
        if not base_spec:
            raise EnvArenaError(f"Base harness {base_harness_ref.key}@{base_harness_ref.version} has no spec.")
        existing_skill_refs = [] if job_config.get("replace_skill_refs") else list(base_spec.get("skill_refs", []))
        merged_refs: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for ref in existing_skill_refs + [ref.to_dict() for ref in skill_refs]:
            parsed = self._as_version_ref(ref)
            key = (parsed.key, parsed.version)
            if key not in seen:
                merged_refs.append(parsed.to_dict())
                seen.add(key)
        base_spec["skill_refs"] = merged_refs
        metadata = dict(base_spec.get("metadata", {}))
        metadata.update(
            {
                "source_stream_id": source_stream_id,
                "base_harness_ref": base_harness_ref.to_dict(),
                "generated_by": "areal-rsi",
                **dict(extra_metadata or {}),
            }
        )
        base_spec["metadata"] = metadata
        key = str(
            job_config.get("harness_key") if key_seed is None and job_config.get("harness_key")
            else self._derived_asset_key(key_seed or f"{base_harness_ref.key}-fork-harness")
        )
        version = str(job_config.get("harness_version") or "1.0.0")
        self.create_harness(
            key=key,
            name=str(job_config.get("harness_name") or key),
            description=str(job_config.get("harness_description") or f"AReaL-RSI fork of {base_harness_ref.key}"),
            tags=list(job_config.get("harness_entity_tags", ["areal-rsi", "stream-fork"])),
            metadata={"source_stream_id": source_stream_id, "base_harness_ref": f"{base_harness_ref.key}@{base_harness_ref.version}"},
        )
        self.create_harness_version(key, version=version, spec=base_spec)
        self.publish_harness_version(key, version)
        return EnvArenaVersionRef(key=key, version=version)

    def _create_fork_stream_record(
        self,
        *,
        fork_stream_id: str,
        source_stream: Mapping[str, Any],
        benchmark_ref: EnvArenaVersionRef | None,
        harness_ref: EnvArenaVersionRef,
        reward_ref: EnvArenaVersionRef,
        data_items: Sequence[Mapping[str, Any]],
        skill_refs: Sequence[EnvArenaVersionRef],
        metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Create a legacy fork stream metadata record.

        Parameters:
            fork_stream_id: New fork stream ID.
            source_stream: Source stream object.
            benchmark_ref: Optional benchmark ref copied into metadata.
            harness_ref: Default harness ref for the fork stream.
            reward_ref: Default reward ref for the fork stream.
            data_items: Selected source data items.
            skill_refs: Skill refs associated with generated behavior.
            metadata: Additional metadata and optional fork name/description.

        Returns:
            Created stream payload.
        """

        source_stream_id = str(source_stream.get("stream_id", ""))
        return self.create_stream(
            stream_id=fork_stream_id,
            name=str(metadata.get("fork_stream_name") or f"{source_stream.get('name', source_stream_id)} fork"),
            description=str(metadata.get("fork_stream_description") or f"AReaL-RSI fork of stream {source_stream_id}"),
            task=str(source_stream.get("task") or ""),
            selector=dict(source_stream.get("selector", {})),
            default_harness_ref=harness_ref,
            default_reward_ref=reward_ref,
            metadata={
                **dict(metadata),
                "source_stream_id": source_stream_id,
                "data_ids": json.dumps([str(item.get("data_id", "")) for item in data_items], ensure_ascii=True),
                "skill_refs": json.dumps([ref.to_dict() for ref in skill_refs], ensure_ascii=True),
                "generated_by": "areal-rsi",
                **({"benchmark_ref": f"{benchmark_ref.key}@{benchmark_ref.version}"} if benchmark_ref else {}),
            },
        )

    @staticmethod
    def _derived_asset_key(seed: str) -> str:
        """Create an AReaL-RSI registry key from a free-form seed.

        Parameters:
            seed: Free-form text used to derive the stable prefix.

        Returns:
            Lowercase registry key with unsafe characters replaced and an
            eight-character random suffix.
        """

        normalized = re.sub(r"[^A-Za-z0-9_.-]", "-", seed.strip()).strip("-").lower()
        normalized = normalized[:80] or "stream-eval"
        return f"areal-rsi-{normalized}-{uuid4().hex[:8]}"

    def list_models(self, *, disable_stream: bool = True) -> list[dict[str, Any]]:
        """List model routes configured in EnvArena.

        ``disable_stream`` excludes dynamically registered stream model names,
        which keeps the result suitable for selecting a stable RSI model route.
        """

        payload = self._unwrap_generated(
            get_openapi_v1_llm_models.sync_detailed(
                client=self._client,
                disable_stream=disable_stream,
            ),
            "list llm models",
            {200},
        )
        return list(payload.get("items", []))

    def list_online_harnesses(self) -> list[str]:
        payload = self._unwrap_generated(
            get_openapi_v1_online_harnesses.sync_detailed(client=self._client),
            "list online harnesses",
            {200},
        )
        return list(payload.get("harness", []))

    def list_benchmarks(self, *, tag: list[str] | None = None) -> list[dict[str, Any]]:
        return self._list_registry("benchmarks", tag=tag)

    def list_skills(self, *, tag: list[str] | None = None) -> list[dict[str, Any]]:
        return self._list_registry("skills", tag=tag)

    def list_harnesses(self, *, tag: list[str] | None = None) -> list[dict[str, Any]]:
        return self._list_registry("harnesses", tag=tag)

    def list_benchmark_versions(self, key: str, *, status: str = "PUBLISHED") -> list[dict[str, Any]]:
        return self._list_versions("benchmarks", key, status=status)

    def list_skill_versions(self, key: str, *, status: str = "PUBLISHED") -> list[dict[str, Any]]:
        return self._list_versions("skills", key, status=status)

    def list_harness_versions(self, key: str, *, status: str = "PUBLISHED") -> list[dict[str, Any]]:
        return self._list_versions("harnesses", key, status=status)

    def _submit_benchmark_job(
        self,
        *,
        benchmark_ref: EnvArenaVersionRef | Mapping[str, Any] | str,
        harness_ref: EnvArenaVersionRef | Mapping[str, Any] | str,
        model_name: str | None = None,
        model_ref: EnvArenaVersionRef | Mapping[str, Any] | str | None = None,
        job_id: str | None = None,
        sample_numbers: int = 1,
        concurrency_policy: Mapping[str, Any] | None = None,
        tags: list[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
        model_parameters: Mapping[str, Any] | None = None,
        model_env: Mapping[str, str] | None = None,
        assets: Mapping[str, Any] | None = None,
        job_config: Mapping[str, Any] | None = None,
    ) -> EvalJob:
        benchmark, harness = self._as_version_ref(benchmark_ref), self._as_version_ref(harness_ref)
        registry_model = self._as_version_ref(model_ref) if model_ref is not None else None
        inline_model_name = (model_name or "").strip()
        if not inline_model_name and registry_model is None:
            raise EnvArenaConfigurationError(
                "BenchmarkJob submission requires a runtime model_name or a published model_ref."
            )
        model: ModelSpec | Any = UNSET
        if inline_model_name:
            model = ModelSpec(model_name=inline_model_name)
            if model_parameters:
                model.additional_properties["parameters"] = dict(model_parameters)
            if model_env:
                model.additional_properties["env"] = dict(model_env)
        elif model_parameters or model_env:
            # EnvArena permits inline values to override a referenced Model spec.
            model = ModelSpec(model_name="")
            if model_parameters:
                model.additional_properties["parameters"] = dict(model_parameters)
            if model_env:
                model.additional_properties["env"] = dict(model_env)
        body = CreateJobRequest(
            benchmark_ref=VersionRef(**benchmark.to_dict()),
            harness_ref=VersionRef(**harness.to_dict()),
            model_ref=VersionRef(**registry_model.to_dict()) if registry_model is not None else UNSET,
            model=model,
            job_id=job_id if job_id else UNSET,
            sample_numbers=sample_numbers,
            concurrency_policy=self._concurrency_policy(concurrency_policy),
            tags=tags if tags else UNSET,
        )
        if metadata:
            body.additional_properties["metadata"] = self._string_metadata(metadata)
        payload = self._unwrap_generated(
            post_openapi_v1_jobs.sync_detailed(client=self._client, body=body),
            "submit benchmark job",
            {202},
        )
        job = payload.get("job", {})
        return {
            "eval_job_id": str(payload.get("job_id", "")),
            "status": job.get("status", "PENDING"),
            "model_ref": {
                "model_name": inline_model_name or registry_model.key,
                "runtime_model_name": inline_model_name or registry_model.key,
                **({"registry_ref": registry_model.to_dict()} if registry_model is not None else {}),
            },
            "benchmark": benchmark.key,
            "benchmark_ref": benchmark.to_dict(),
            "harness_ref": harness.to_dict(),
            "harness_config": {"harness_ref": harness.to_dict()},
            "task_ids": list(payload.get("task_ids", [])),
            "assets": dict(assets or {}),
            "job_config": dict(job_config or {}),
            "metadata": dict(metadata or {}),
            "raw": payload,
        }

    @staticmethod
    def _concurrency_policy(concurrency_policy: Mapping[str, Any] | None) -> ConcurrencyPolicy | Any:
        if concurrency_policy is None:
            return UNSET
        return ConcurrencyPolicy(
            max_parallel=int(concurrency_policy.get("max_parallel", 1)),
            max_retries=int(concurrency_policy.get("max_retries", 0)),
            retry_backoff_seconds=int(concurrency_policy.get("retry_backoff_seconds", 30)),
        )

    def submit_eval(self, request: EvalRequest) -> EvalJob:
        job_config = self._merge_job_config(self.config.job_config, request.job_config)
        skill_ref = self._resolve_optional_registry_asset("skills", request.skill)
        benchmark_ref = self._resolve_registry_ref("benchmarks", request.benchmark, "benchmark")
        harness_ref = self._resolve_registry_ref("harnesses", request.harness, "harness")
        model = self._resolve_model_asset(request.model)
        return self._submit_benchmark_job(
            benchmark_ref=benchmark_ref,
            harness_ref=harness_ref,
            model_name=model.get("model_name") or None,
            model_ref=model.get("registry_ref"),
            job_id=job_config.get("job_id"),
            sample_numbers=int(job_config.get("sample_numbers", 1)),
            concurrency_policy=job_config.get("concurrency_policy"),
            tags=list(job_config.get("tags", [])),
            metadata=request.metadata,
            model_parameters=job_config.get("model_parameters") or model.get("parameters"),
            model_env=job_config.get("model_env") or model.get("env"),
            assets={
                "model": model["asset"],
                "benchmark": benchmark_ref.to_dict(),
                "harness": harness_ref.to_dict(),
                **({"skill": skill_ref.to_dict()} if skill_ref is not None else {}),
            },
            job_config=job_config,
        )

    @staticmethod
    def _merge_job_config(
        defaults: Mapping[str, Any],
        overrides: Mapping[str, Any],
    ) -> dict[str, Any]:
        merged = deepcopy(dict(defaults))
        for key, value in overrides.items():
            if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
                nested = deepcopy(dict(merged[key]))
                nested.update(dict(value))
                merged[key] = nested
            else:
                merged[key] = deepcopy(value)
        return merged

    def _resolve_optional_registry_asset(
        self, collection: str, config: Mapping[str, Any] | None
    ) -> EnvArenaVersionRef | None:
        if config is None:
            return None
        asset = self._asset_config(config, collection.removesuffix("s"))
        if asset["type"] == "ref":
            return self._registry_ref_from_asset(asset, collection)
        return self._create_registry_asset(collection, asset)

    def _resolve_registry_ref(
        self,
        collection: str,
        config: EnvArenaVersionRef | Mapping[str, Any] | str,
        asset_name: str,
    ) -> EnvArenaVersionRef:
        if isinstance(config, EnvArenaVersionRef):
            return config
        if isinstance(config, str):
            return self._as_version_ref(config)
        if not isinstance(config, Mapping):
            raise EnvArenaConfigurationError(f"{asset_name} must be an Arena ref.")
        if config.get("type") == "new":
            raise EnvArenaConfigurationError(f"{asset_name} must be an existing Arena ref; type='new' is not supported.")
        asset = dict(config)
        if asset.get("type") == "ref":
            return self._registry_ref_from_asset(asset, collection)
        ref = self._optional_registry_ref_from_asset(asset)
        if ref is None:
            raise EnvArenaConfigurationError(
                f"{asset_name} refs require ref, registry_ref, or key/version."
            )
        return ref

    def _resolve_model_asset(self, config: Mapping[str, Any]) -> dict[str, Any]:
        asset = self._asset_config(config, "model")
        if asset["type"] == "ref":
            registry_ref = self._optional_registry_ref_from_asset(asset)
            model_name = str(asset.get("model_name") or asset.get("runtime_model_name") or "")
            if registry_ref is None and not model_name and asset.get("ref") is not None:
                ref_text = str(asset["ref"])
                model_name = "" if "@" in ref_text else ref_text
            if registry_ref is None and not model_name:
                raise EnvArenaConfigurationError(
                    "model ref assets require ref/key/version for a registry model or model_name."
                )
            return {
                "model_name": model_name,
                **({"registry_ref": registry_ref} if registry_ref is not None else {}),
                "parameters": asset.get("parameters", {}),
                "env": asset.get("env", {}),
                "asset": {
                    **({"model_name": model_name} if model_name else {}),
                    **({"registry_ref": registry_ref.to_dict()} if registry_ref is not None else {}),
                },
            }
        if "spec" in asset:
            registry_ref = self._create_registry_asset("models", asset)
            return {
                "model_name": "",
                "registry_ref": registry_ref,
                "parameters": asset.get("parameters", {}),
                "env": asset.get("env", {}),
                "asset": {"registry_ref": registry_ref.to_dict()},
            }
        model_path = str(asset.get("model_path") or asset.get("path") or "").strip()
        if not model_path:
            raise EnvArenaConfigurationError(
                "model new assets require model_path/path, or spec for a registry model."
            )
        model_instance = self._start_model_instance(model_path, asset)
        model_name = str(model_instance["model_name"])
        return {
            "model_name": model_name,
            "parameters": asset.get("parameters", {}),
            "env": asset.get("env", {}),
            "asset": model_instance,
        }

    def _start_model_instance(self, model_path: str, config: Mapping[str, Any]) -> dict[str, Any]:
        """Mock EnvArena model-instance startup until the control-plane API exists."""

        model_name = str(config.get("model_name") or self._model_instance_name_for(model_path))
        return {
            "model_name": model_name,
            "model_path": model_path,
            "status": "mock_started",
            "backend": "mock_envarena_model_instance",
        }

    def _create_registry_asset(
        self,
        collection: str,
        asset: Mapping[str, Any],
        *,
        spec: Mapping[str, Any] | None = None,
    ) -> EnvArenaVersionRef:
        key = self._required_asset_string(asset, "key")
        version = self._required_asset_string(asset, "version")
        if bool(asset.get("create_entity", True)):
            entity: dict[str, Any] = {"key": key, "name": str(asset.get("name") or key)}
            for field_name in ("description", "tags", "metadata"):
                if field_name in asset:
                    entity[field_name] = asset[field_name]
            self._request_json(
                "POST",
                f"/openapi/v1/{collection}",
                json_body=entity,
                operation=f"create {collection} key",
                expected_statuses={200, 201, 204},
            )
        version_body: dict[str, Any] = {
            "version": version,
            "spec": dict(spec or self._asset_spec(asset, collection.removesuffix("s"))),
        }
        if "supersedes" in asset:
            version_body["supersedes"] = asset["supersedes"]
        self._request_json(
            "POST",
            f"/openapi/v1/{collection}/{quote(key, safe='')}/versions",
            json_body=version_body,
            operation=f"create {collection} version",
            expected_statuses={200, 201},
        )
        if bool(asset.get("publish", True)):
            self._request_json(
                "POST",
                f"/openapi/v1/{collection}/{quote(key, safe='')}/versions/{quote(version, safe='')}/publish",
                operation=f"publish {collection} version",
                expected_statuses={200},
            )
        return EnvArenaVersionRef(key=key, version=version)

    @staticmethod
    def _asset_config(config: Mapping[str, Any], asset_name: str) -> dict[str, Any]:
        if not isinstance(config, Mapping):
            raise EnvArenaConfigurationError(f"{asset_name} asset config must be a mapping.")
        asset = dict(config)
        config_type = asset.get("type")
        if config_type not in {"new", "ref"}:
            raise EnvArenaConfigurationError(
                f"{asset_name} asset config requires type='new' or type='ref'."
            )
        return asset

    @staticmethod
    def _asset_spec(asset: Mapping[str, Any], asset_name: str) -> dict[str, Any]:
        spec = asset.get("spec")
        if not isinstance(spec, Mapping):
            raise EnvArenaConfigurationError(
                f"{asset_name} new asset config requires a mapping 'spec'."
            )
        return dict(spec)

    def _registry_ref_from_asset(
        self, asset: Mapping[str, Any], collection: str
    ) -> EnvArenaVersionRef:
        ref = self._optional_registry_ref_from_asset(asset)
        if ref is None:
            raise EnvArenaConfigurationError(
                f"{collection} ref assets require ref, registry_ref, or key/version."
            )
        return ref

    def _optional_registry_ref_from_asset(
        self, asset: Mapping[str, Any]
    ) -> EnvArenaVersionRef | None:
        if asset.get("registry_ref") is not None:
            return self._as_version_ref(asset["registry_ref"])
        if asset.get("ref") is not None:
            if isinstance(asset["ref"], Mapping):
                return self._as_version_ref(asset["ref"])
            ref_text = str(asset["ref"])
            return self._as_version_ref(ref_text) if "@" in ref_text else None
        if asset.get("key") is not None and asset.get("version") is not None:
            return EnvArenaVersionRef(key=str(asset["key"]), version=str(asset["version"]))
        return None

    @staticmethod
    def _required_asset_string(asset: Mapping[str, Any], field_name: str) -> str:
        value = str(asset.get(field_name, "")).strip()
        if not value:
            raise EnvArenaConfigurationError(
                f"new asset config requires a non-empty {field_name!r} field."
            )
        return value

    def _model_instance_name_for(self, model_path: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9_.-]", "-", model_path.strip()).strip("-")
        return f"{self.config.model_instance_name_prefix}{normalized or 'model'}"[:128]

    def get_job_summary(self, job_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/openapi/v1/jobs/{job_id}/summary", operation="get job summary")

    def get_job_result(self, job_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/openapi/v1/jobs/{job_id}/result", operation="get job result")

    def get_eval_progress(self, job: EvalJob | str) -> dict[str, Any]:
        """Return EnvArena's current summary for an evaluation job or job ID."""

        return self.get_job_summary(self._job_id(job))

    def get_eval_result(self, job: EvalJob | str) -> dict[str, Any]:
        """Return EnvArena's aggregate result for an evaluation job or job ID."""

        return self.get_job_result(self._job_id(job))

    def list_job_tasks(self, job_id: str, *, status: str = "ALL") -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        offset = 0
        while True:
            payload = self._request_json(
                "GET",
                f"/openapi/v1/jobs/{job_id}/tasks",
                params={"status": status, "limit": 100, "offset": offset},
                operation="list job tasks",
            )
            page = list(payload.get("items", []))
            items.extend(page)
            offset += len(page)
            if not page or offset >= int(payload.get("total", len(items))):
                return items

    def list_job_trajectories(self, job_id: str, *, inline: bool = False) -> list[dict[str, Any]]:
        return list(
            self._request_json(
                "GET",
                f"/openapi/v1/jobs/{job_id}/trajectories",
                params={"inline": str(inline).lower()},
                operation="list job trajectories",
            ).get("tasks", [])
        )

    def get_task_trajectory(self, job_id: str, task_id: str, *, attempt: int | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"format": "json"}
        if attempt is not None:
            params["attempt"] = attempt
        return self._request_json(
            "GET",
            f"/openapi/v1/jobs/{job_id}/tasks/{task_id}/trajectory",
            params=params,
            operation="get task trajectory",
        )

    def get_trajs(self, job: EvalJob | str, include_messages: bool = False) -> list[Traj]:
        job_id = self._job_id(job)
        tasks = {str(task.get("task_id")): task for task in self.list_job_tasks(job_id)}
        job_payload = job if isinstance(job, Mapping) else {}
        model_ref = job_payload.get("model_ref", {})
        model_name = str(model_ref.get("model_name") or model_ref.get("runtime_model_name") or "")
        source = str(job_payload.get("metadata", {}).get("source", "eval"))
        trajs: list[Traj] = []
        for trajectory in self.list_job_trajectories(job_id, inline=include_messages):
            task_id = str(trajectory.get("task_id", ""))
            task, attempt = tasks.get(task_id, {}), trajectory.get("latest_attempt")
            traj: Traj = {
                "traj_id": f"{job_id}:{task_id}:{attempt or 0}",
                "task_id": task_id,
                "model_ref": model_name,
                "harness_id": job_payload.get("harness_ref", {}).get("key"),
                "reward": task.get("score"),
                "status": task.get("status", trajectory.get("status")),
                "metadata": {
                    "source": source,
                    "eval_job_id": job_id,
                    "trajectory_uri": trajectory.get("trajectory_uri"),
                    "attempt": attempt,
                    "event_count": trajectory.get("event_count"),
                },
                "raw": {"task": task, "trajectory": trajectory},
            }
            if include_messages:
                traj["messages"] = list(trajectory.get("events", []))
            trajs.append(traj)
        return trajs

    def _list_registry(self, asset: str, *, tag: list[str] | None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        offset = 0
        while True:
            params: dict[str, Any] = {"limit": 200, "offset": offset}
            if tag:
                params["tag"] = tag
            payload = self._request_json("GET", f"/openapi/v1/{asset}", params=params, operation=f"list {asset}")
            page = list(payload.get("items", []))
            items.extend(page)
            offset += len(page)
            if not page or offset >= int(payload.get("total", len(items))):
                return items

    def _list_versions(self, asset: str, key: str, *, status: str) -> list[dict[str, Any]]:
        return list(
            self._request_json(
                "GET",
                f"/openapi/v1/{asset}/{key}/versions",
                params={"status": status},
                operation=f"list {asset} versions",
            ).get("items", [])
        )

    def _default_llm_gateway_base_url(self) -> str:
        base_url = self.config.control_base_url.rstrip("/")
        if base_url.endswith("/openapi/v1/llm"):
            return base_url
        if base_url.endswith("/openapi/v1"):
            return f"{base_url}/llm"
        return f"{base_url}/openapi/v1/llm"

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        operation: str,
        expected_statuses: set[int] | None = None,
    ) -> dict[str, Any]:
        expected = expected_statuses or {200}
        try:
            response = self._client.get_httpx_client().request(
                method,
                path,
                params=params,
                json=dict(json_body) if json_body is not None else None,
            )
        except httpx.HTTPError as exc:
            raise EnvArenaError(f"EnvArena {operation} transport error: {exc}") from exc
        payload = self._decode_json(response, operation)
        if response.status_code not in expected:
            raise EnvArenaHTTPError(operation, response.status_code, payload)
        if not isinstance(payload, dict):
            raise EnvArenaError(f"EnvArena {operation} returned a non-object JSON payload.")
        return payload

    @staticmethod
    def _decode_json(response: httpx.Response, operation: str) -> Any:
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"error": f"non-JSON response for {operation}", "body": response.text[:1000]}

    def _unwrap_generated(self, response: Any, operation: str, expected_statuses: set[int]) -> dict[str, Any]:
        payload = response.parsed.to_dict() if hasattr(response.parsed, "to_dict") else {"error": "non-JSON response"}
        if int(response.status_code) not in expected_statuses:
            raise EnvArenaHTTPError(operation, int(response.status_code), payload)
        if not isinstance(payload, dict):
            raise EnvArenaError(f"EnvArena {operation} returned an unexpected payload.")
        return payload

    @staticmethod
    def _string_metadata(metadata: Mapping[str, Any]) -> dict[str, str]:
        return {str(key): str(value) for key, value in metadata.items()}

    @staticmethod
    def _as_version_ref(value: EnvArenaVersionRef | Mapping[str, Any] | str) -> EnvArenaVersionRef:
        return value if isinstance(value, EnvArenaVersionRef) else EnvArenaVersionRef.parse(value)

    @staticmethod
    def _job_id(job: EvalJob | str) -> str:
        if isinstance(job, str):
            job_id = job.strip()
        else:
            job_id = str(job.get("eval_job_id") or job.get("job_id") or "")
        if not job_id:
            raise EnvArenaConfigurationError("EvalJob has no eval_job_id/job_id.")
        return job_id
