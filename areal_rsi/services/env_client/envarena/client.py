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

    def _launch_batch_task_item(self, task: Mapping[str, Any] | str) -> LaunchBatchTaskItem:
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
        return self._request_json(
            "GET",
            f"/openapi/v1/model-services/{quote(service_id, safe='')}",
            operation="get model service",
        )

    def stop_model_service(self, service_id: str) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"/openapi/v1/model-services/{quote(service_id, safe='')}/stop",
            operation="stop model service",
        )

    def stop_model_service_instance(self, service_id: str, instance_id: str) -> dict[str, Any]:
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
        instances = payload.get("instances")
        if not isinstance(instances, list):
            return None
        for instance in instances:
            if isinstance(instance, Mapping) and str(instance.get("instance_id") or "") == instance_id:
                return dict(instance)
        return None

    def delete_model_service(self, service_id: str) -> dict[str, Any]:
        return self._request_json(
            "DELETE",
            f"/openapi/v1/model-services/{quote(service_id, safe='')}",
            operation="delete model service",
            expected_statuses={204},
        )

    def ensure_model_service_for_ckpt(
        self,
        ckpt: str | Mapping[str, Any],
        *,
        job_config: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        ckpt_config = dict(ckpt) if isinstance(ckpt, Mapping) else {"model_path": str(ckpt)}
        if ckpt_config.get("type") == "ref" or (
            "model_path" not in ckpt_config and any(key in ckpt_config for key in ("ref", "registry_ref", "key", "version"))
        ):
            registry_ref = self._optional_registry_ref_from_asset(ckpt_config)
            model_name = str(ckpt_config.get("model_name") or ckpt_config.get("served_model_name") or "")
            if registry_ref is None and ckpt_config.get("ref") is not None and "@" not in str(ckpt_config["ref"]):
                model_name = str(ckpt_config["ref"])
            if registry_ref is None and not model_name:
                raise EnvArenaConfigurationError("ckpt ref requires model_name/served_model_name or registry ref.")
            return {
                "model_name": model_name,
                **({"registry_ref": registry_ref} if registry_ref is not None else {}),
                "asset": {
                    **({"model_name": model_name} if model_name else {}),
                    **({"registry_ref": registry_ref.to_dict()} if registry_ref is not None else {}),
                },
            }
        if ckpt_config.get("model_name") and "model_path" not in ckpt_config:
            model_name = str(ckpt_config["model_name"])
            return {"model_name": model_name, "asset": {"model_name": model_name}}

        model_service_overrides = (job_config or {}).get("model_service") or {}
        if not isinstance(model_service_overrides, Mapping):
            raise EnvArenaConfigurationError("job_config.model_service must be a mapping when set.")
        model_service_config = self._merge_job_config(
            self.config.model_service_config,
            model_service_overrides,
        )
        model_service_config = self._merge_job_config(model_service_config, ckpt_config)
        base_model_id = str(model_service_config.get("base_model_id") or "").strip()
        model_path = str(model_service_config.get("model_path") or model_service_config.get("path") or "").strip()
        create_model_service_enabled = bool(
            model_service_config.get("enabled")
            or model_service_config.get("create")
            or model_service_config.get("create_service")
        )
        if not model_path and not (base_model_id and create_model_service_enabled):
            raise EnvArenaConfigurationError("ckpt must be a model path, runtime model ref, or registry model ref.")
        served_model_name = str(
            model_service_config.get("served_model_name")
            or model_service_config.get("model_name")
            or (self._model_instance_name_for(model_path) if model_path else "")
        )
        if create_model_service_enabled:
            service = self.create_model_service(
                served_model_name=served_model_name or None,
                model_path=model_path or None,
                base_model_id=base_model_id or None,
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
        ckpt: str | Mapping[str, Any],
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
        raw_registry_config = (job_config or {}).get("model_registry") or {}
        if not isinstance(raw_registry_config, Mapping):
            raise EnvArenaConfigurationError("job_config.model_registry must be a mapping when set.")
        registry_config = dict(raw_registry_config)
        service_job_config = self._merge_job_config(job_config or {}, {"model_service": {"enabled": True}})
        model = self.ensure_model_service_for_ckpt(ckpt, job_config=service_job_config, metadata=metadata)
        existing_ref = model.get("registry_ref")
        if existing_ref is not None:
            registry_ref = self._as_version_ref(existing_ref)
            return {
                "model_name": model.get("model_name", ""),
                "registry_ref": registry_ref.to_dict(),
                "asset": model.get("asset", {}),
            }

        model_name = str(model.get("model_name") or "").strip()
        if not model_name:
            raise EnvArenaError("Cannot create a model registry version without a runtime model name.")
        asset = dict(model.get("asset", {}))
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

    def submit_eval_by_existed_stream(
        self,
        ckpt: str | Mapping[str, Any],
        stream_id: str,
        *,
        data_ids: Sequence[str] | None = None,
        job_config: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> EvalJob:
        return self._submit_stream_eval(
            EnvArenaEvalRequest(
                ckpt=ckpt,
                stream_id=stream_id,
                data_ids=tuple(data_ids or ()),
                job_config=dict(job_config or {}),
                metadata=dict(metadata or {}),
            ),
            fork=False,
        )

    def submit_eval_by_fork_stream(
        self,
        ckpt: str | Mapping[str, Any],
        stream_id: str,
        data_ids: Sequence[str],
        skills: Sequence[Mapping[str, Any] | str],
        relationships: Sequence[Mapping[str, Any]],
        reward: Mapping[str, Any] | str | None = None,
        *,
        job_config: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> EvalJob:
        fork = self._fork_stream_for_eval(
            EnvArenaEvalRequest(
                ckpt=ckpt,
                stream_id=stream_id,
                data_ids=tuple(data_ids),
                skills=tuple(skills),
                relationships=tuple(relationships),
                reward=reward,
                job_config=dict(job_config or {}),
                metadata=dict(metadata or {}),
            )
        )
        fork_stream_id = str(fork["stream_id"])
        fork_data_ids = [str(item["data_id"]) for item in fork["items"]]
        job_metadata = {
            **dict(metadata or {}),
            "source_stream_id": stream_id,
            "fork_stream_id": fork_stream_id,
        }
        job = self.submit_eval_by_existed_stream(
            ckpt,
            fork_stream_id,
            data_ids=fork_data_ids,
            job_config=job_config,
            metadata=job_metadata,
        )
        job["fork_stream_id"] = fork_stream_id
        job["source_stream_id"] = stream_id
        job["assets"] = {
            **dict(job.get("assets", {})),
            "fork_stream": fork,
            "relationships": [dict(item) for item in relationships],
        }
        return job

    def _fork_stream_for_eval(self, request: EnvArenaEvalRequest) -> dict[str, Any]:
        if not request.data_ids:
            raise EnvArenaConfigurationError("fork stream evaluation requires at least one data_id.")

        job_config = self._merge_job_config(self.config.job_config, self.config.stream_eval_config)
        job_config = self._merge_job_config(job_config, request.job_config)
        source_stream = self._stream_object(self.get_stream(request.stream_id))
        source_stream_id = str(source_stream.get("stream_id") or request.stream_id)
        selected_items = self._select_stream_dataset_items(
            source_stream_id,
            requested_data_ids=request.data_ids,
            selector=job_config.get("selector"),
            page_size=int(job_config.get("dataset_page_size", 1000)),
        )
        reward_ref = self._resolve_stream_reward_ref(source_stream, request.reward, job_config)
        base_harness_ref = self._resolve_stream_harness_ref(source_stream, job_config)
        relationships_by_data_id = self._relationships_by_data_id(request.relationships)
        global_skill_refs, skill_refs_by_data_id = self._resolve_stream_skill_bindings(
            request.skills,
            relationships=request.relationships,
        )
        harness_cache: dict[tuple[tuple[str, str], ...], EnvArenaVersionRef] = {}

        def harness_for_skill_refs(
            skill_refs: Sequence[EnvArenaVersionRef],
            *,
            data_id: str | None = None,
        ) -> EnvArenaVersionRef:
            key = tuple((ref.key, ref.version) for ref in skill_refs)
            if not key:
                return base_harness_ref
            if key not in harness_cache:
                harness_cache[key] = self._create_fork_harness(
                    base_harness_ref,
                    skill_refs,
                    source_stream_id,
                    job_config,
                    key_seed=f"{base_harness_ref.key}-{data_id}-fork-harness" if data_id else None,
                    extra_metadata={"data_id": data_id} if data_id else None,
                )
            return harness_cache[key]

        default_harness_ref = harness_for_skill_refs(global_skill_refs) if global_skill_refs else base_harness_ref
        fork_items: list[dict[str, Any]] = []
        for item in selected_items:
            data_id = str(item.get("data_id", "")).strip()
            if not data_id:
                raise EnvArenaConfigurationError("stream dataset item is missing data_id.")
            relationships = relationships_by_data_id.get(data_id, [])
            task_harness_ref = self._stream_item_or_relationship_ref(
                item,
                relationships,
                field_names=("harness_ref", "harness"),
                collection="harnesses",
                asset_name="harness",
            )
            if task_harness_ref is None:
                task_skill_refs = self._dedupe_refs([*global_skill_refs, *skill_refs_by_data_id.get(data_id, [])])
                task_harness_ref = (
                    default_harness_ref
                    if task_skill_refs == global_skill_refs
                    else harness_for_skill_refs(task_skill_refs, data_id=data_id)
                )
            fork_items.append(
                {
                    "data_id": data_id,
                    "harness_ref": task_harness_ref.to_dict(),
                    "env_ref": self._env_ref_for_stream_item(item).to_dict(),
                }
            )

        fork_stream_id = str(job_config.get("fork_stream_id") or self._derived_asset_key(f"{source_stream_id}-fork"))
        return self.fork_stream(
            source_stream_id,
            fork_items,
            stream_id=fork_stream_id,
            name=str(request.metadata.get("fork_stream_name") or f"{source_stream.get('name', source_stream_id)} fork"),
            description=str(request.metadata.get("fork_stream_description") or f"AReaL-RSI fork of stream {source_stream_id}"),
            default_harness_ref=default_harness_ref,
            default_reward_ref=reward_ref,
            selector=job_config.get("selector"),
            metadata={
                **dict(request.metadata),
                "relationships": json.dumps([dict(item) for item in request.relationships], ensure_ascii=True),
            },
        )

    def _submit_stream_eval(self, request: EnvArenaEvalRequest, *, fork: bool) -> EvalJob:
        if fork and not request.data_ids:
            raise EnvArenaConfigurationError("fork stream evaluation requires at least one data_id.")

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
        reward_ref = self._resolve_stream_reward_ref(stream, request.reward, job_config)
        base_harness_ref = self._resolve_stream_harness_ref(stream, job_config)
        global_skill_refs, skill_refs_by_data_id = (
            self._resolve_stream_skill_bindings(request.skills) if fork else ([], {})
        )
        harness_cache: dict[tuple[tuple[str, str], ...], EnvArenaVersionRef] = {}

        def harness_for_skill_refs(
            skill_refs: Sequence[EnvArenaVersionRef],
            *,
            data_id: str | None = None,
        ) -> EnvArenaVersionRef:
            key = tuple((ref.key, ref.version) for ref in skill_refs)
            if not key:
                return base_harness_ref
            if key not in harness_cache:
                harness_cache[key] = self._create_fork_harness(
                    base_harness_ref,
                    skill_refs,
                    source_stream_id,
                    job_config,
                    key_seed=f"{base_harness_ref.key}-{data_id}-fork-harness" if data_id else None,
                    extra_metadata={"data_id": data_id} if data_id else None,
                )
            return harness_cache[key]

        harness_ref = (
            harness_for_skill_refs(global_skill_refs)
            if fork and global_skill_refs
            else base_harness_ref
        )
        fork_stream_id = (
            str(job_config.get("fork_stream_id") or self._derived_asset_key(f"{stream_id}-fork"))
            if fork
            else None
        )
        relationships_by_data_id = self._relationships_by_data_id(request.relationships)
        tasks: list[dict[str, Any]] = []
        all_skill_refs: list[EnvArenaVersionRef] = list(global_skill_refs)
        for item in selected_items:
            data_id = str(item.get("data_id", "")).strip()
            if not data_id:
                raise EnvArenaConfigurationError("stream dataset item is missing data_id.")
            relationships = relationships_by_data_id.get(data_id, [])
            relationship_skill_refs = self._resolve_relationship_skill_refs(relationships)
            task_skill_refs = self._dedupe_refs(
                [*global_skill_refs, *skill_refs_by_data_id.get(data_id, []), *relationship_skill_refs]
            )
            all_skill_refs = self._dedupe_refs([*all_skill_refs, *task_skill_refs])
            task_harness_ref = self._stream_item_or_relationship_ref(
                item,
                relationships,
                field_names=("harness_ref", "harness"),
                collection="harnesses",
                asset_name="harness",
            )
            if task_harness_ref is None and task_skill_refs != global_skill_refs:
                task_harness_ref = harness_for_skill_refs(task_skill_refs, data_id=data_id)
            task_reward_ref = self._stream_item_or_relationship_ref(
                item,
                relationships,
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

        fork_stream: dict[str, Any] | None = None
        if fork and fork_stream_id is not None:
            fork_stream = self._create_fork_stream_record(
                fork_stream_id=fork_stream_id,
                source_stream=stream,
                benchmark_ref=None,
                harness_ref=harness_ref,
                reward_ref=reward_ref,
                data_items=selected_items,
                skill_refs=all_skill_refs,
                relationships=request.relationships,
                metadata=request.metadata,
            )

        model = self.ensure_model_registry_for_ckpt(
            request.ckpt,
            job_config=job_config,
            metadata={
                **dict(request.metadata),
                "source_stream_id": source_stream_id,
                **({"fork_stream_id": fork_stream_id} if fork_stream_id else {}),
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
            "stream_eval_mode": "fork" if fork else "existing",
            "source_stream_id": source_stream_id,
            **({"fork_stream_id": fork_stream_id} if fork_stream_id else {}),
        }
        launch_stream_id = fork_stream_id or stream_id
        payload = self.launch_stream_batch_task(
            launch_stream_id,
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
            "stream_id": launch_stream_id,
            "source_stream_id": source_stream_id,
            "harness_ref": harness_ref.to_dict(),
            "harness_config": {"harness_ref": harness_ref.to_dict()},
            "reward_ref": reward_ref.to_dict(),
            "task_ids": list(payload.get("task_ids", [])),
            "selected_data_ids": selected_data_ids,
            "assets": {
                "model": model["asset"],
                "harness": harness_ref.to_dict(),
                "reward": reward_ref.to_dict(),
                "stream": stream,
                "data_ids": selected_data_ids,
                "stream_tasks": tasks,
                **({"skills": [ref.to_dict() for ref in all_skill_refs]} if all_skill_refs else {}),
                **({"fork_stream": fork_stream or {"stream_id": fork_stream_id}} if fork_stream_id else {}),
                **({"relationships": [dict(item) for item in request.relationships]} if request.relationships else {}),
            },
            "job_config": dict(job_config),
            "metadata": job_metadata,
            "raw": payload,
        }
        if not job["eval_job_id"]:
            raise EnvArenaError(f"EnvArena launch stream batch task returned no job_id: {payload}")
        if fork_stream_id:
            job["fork_stream_id"] = fork_stream_id
        return job

    @staticmethod
    def _stream_object(payload: Mapping[str, Any]) -> dict[str, Any]:
        stream = payload.get("stream", payload)
        if not isinstance(stream, Mapping):
            raise EnvArenaError("EnvArena get stream returned no stream object.")
        return dict(stream)

    @staticmethod
    def _stream_metadata(stream: Mapping[str, Any]) -> dict[str, Any]:
        metadata = stream.get("metadata", {})
        return dict(metadata) if isinstance(metadata, Mapping) else {}

    def _source_stream_id_for_stream(self, stream: Mapping[str, Any], stream_id: str) -> str:
        metadata = self._stream_metadata(stream)
        return str(metadata.get("source_stream_id") or stream.get("source_stream_id") or stream_id)

    def _fork_stream_items_from_stream(self, stream: Mapping[str, Any]) -> list[dict[str, Any]] | None:
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
        config_ref = job_config.get("harness_ref", job_config.get("harness"))
        if config_ref is not None:
            return self._resolve_registry_ref("harnesses", config_ref, "harness")
        stream_ref = stream.get("default_harness_ref")
        if stream_ref is None:
            raise EnvArenaConfigurationError(
                "stream evaluation requires harness_ref in job_config or stream.default_harness_ref."
            )
        return self._as_version_ref(stream_ref)

    def _resolve_stream_reward_ref(
        self,
        stream: Mapping[str, Any],
        reward_config: Mapping[str, Any] | str | None,
        job_config: Mapping[str, Any],
    ) -> EnvArenaVersionRef:
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

    @staticmethod
    def _relationships_by_data_id(relationships: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        by_data_id: dict[str, list[dict[str, Any]]] = {}
        for relationship in relationships:
            data_id = str(relationship.get("data_id", "")).strip()
            if data_id:
                by_data_id.setdefault(data_id, []).append(dict(relationship))
        return by_data_id

    def _stream_item_or_relationship_ref(
        self,
        item: Mapping[str, Any],
        relationships: Sequence[Mapping[str, Any]],
        *,
        field_names: Sequence[str],
        collection: str,
        asset_name: str,
    ) -> EnvArenaVersionRef | None:
        for field_name in field_names:
            if item.get(field_name) is not None:
                return self._resolve_registry_ref(collection, item[field_name], asset_name)
        for relationship in relationships:
            for field_name in field_names:
                if relationship.get(field_name) is not None:
                    return self._resolve_registry_ref(collection, relationship[field_name], asset_name)
        return None

    def _resolve_stream_skill_bindings(
        self,
        skills: Sequence[Mapping[str, Any] | str],
        *,
        relationships: Sequence[Mapping[str, Any]] = (),
    ) -> tuple[list[EnvArenaVersionRef], dict[str, list[EnvArenaVersionRef]]]:
        global_refs: list[EnvArenaVersionRef] = []
        refs_by_data_id: dict[str, list[EnvArenaVersionRef]] = {}
        data_ids_by_experience_id = self._data_ids_by_experience_id(relationships)
        for skill in skills:
            ref = self._resolve_stream_skill_ref(skill)
            bound_data_ids = self._skill_bound_data_ids(skill, data_ids_by_experience_id)
            if not bound_data_ids:
                global_refs = self._dedupe_refs([*global_refs, ref])
                continue
            for data_id in bound_data_ids:
                refs_by_data_id[data_id] = self._dedupe_refs([*refs_by_data_id.get(data_id, []), ref])
        return global_refs, refs_by_data_id

    def _resolve_stream_skill_refs(
        self,
        skills: Sequence[Mapping[str, Any] | str],
    ) -> list[EnvArenaVersionRef]:
        return self._resolve_stream_skill_bindings(skills)[0]

    def _resolve_stream_skill_ref(self, skill: Mapping[str, Any] | str) -> EnvArenaVersionRef:
        if isinstance(skill, str):
            return self._as_version_ref(skill)
        if isinstance(skill, Mapping) and skill.get("type") in {"new", "ref"}:
            resolved = self._resolve_optional_registry_asset("skills", skill)
            if resolved is None:
                raise EnvArenaConfigurationError("skill config did not resolve to a registry ref.")
            return resolved
        return self._resolve_registry_ref("skills", skill, "skill")

    def _resolve_relationship_skill_refs(
        self,
        relationships: Sequence[Mapping[str, Any]],
    ) -> list[EnvArenaVersionRef]:
        refs: list[EnvArenaVersionRef] = []
        for relationship in relationships:
            for field_name in ("skill_ref", "skill_refs", "skill", "skills"):
                if relationship.get(field_name) is None:
                    continue
                values = self._as_sequence(relationship[field_name])
                for value in values:
                    refs = self._dedupe_refs([*refs, self._resolve_stream_skill_ref(value)])
        return refs

    @staticmethod
    def _data_ids_by_experience_id(relationships: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
        data_ids_by_experience_id: dict[str, list[str]] = {}
        for relationship in relationships:
            experience_id = str(relationship.get("experience_id", "")).strip()
            data_id = str(relationship.get("data_id", "")).strip()
            if experience_id and data_id:
                data_ids_by_experience_id.setdefault(experience_id, []).append(data_id)
        return data_ids_by_experience_id

    @staticmethod
    def _skill_bound_data_ids(
        skill: Mapping[str, Any] | str,
        data_ids_by_experience_id: Mapping[str, Sequence[str]],
    ) -> list[str]:
        if not isinstance(skill, Mapping):
            return []
        data_ids: list[str] = []
        for field_name in ("experience_id", "experience_ids"):
            if skill.get(field_name) is None:
                continue
            for experience_id in EnvArenaClient._as_sequence(skill[field_name]):
                data_ids.extend(str(data_id) for data_id in data_ids_by_experience_id.get(str(experience_id), []))
        if skill.get("data_ids") is not None:
            data_ids.extend(str(data_id) for data_id in EnvArenaClient._as_sequence(skill["data_ids"]))
        if skill.get("data_id") is not None:
            data_ids.append(str(skill["data_id"]))
        return [data_id for data_id in dict.fromkeys(data_ids) if data_id.strip()]

    @staticmethod
    def _as_sequence(value: Any) -> list[Any]:
        if isinstance(value, str) or not isinstance(value, Sequence):
            return [value]
        return list(value)

    @staticmethod
    def _dedupe_refs(refs: Sequence[EnvArenaVersionRef]) -> list[EnvArenaVersionRef]:
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
        relationships_by_data_id: Mapping[str, Sequence[Mapping[str, Any]]],
        fork_stream_id: str | None,
        job_config: Mapping[str, Any],
        request_metadata: Mapping[str, Any],
    ) -> EnvArenaVersionRef:
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
            if relationships_by_data_id.get(data_id):
                params["relationships"] = [dict(item) for item in relationships_by_data_id[data_id]]
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
        relationships: Sequence[Mapping[str, Any]],
        metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
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
                "relationships": json.dumps([dict(item) for item in relationships], ensure_ascii=True),
                "generated_by": "areal-rsi",
                **({"benchmark_ref": f"{benchmark_ref.key}@{benchmark_ref.version}"} if benchmark_ref else {}),
            },
        )

    @staticmethod
    def _derived_asset_key(seed: str) -> str:
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

    def get_eval_progress(self, job: EvalJob) -> dict[str, Any]:
        """Return EnvArena's current summary for a platform-neutral evaluation job."""

        return self.get_job_summary(self._job_id(job))

    def get_eval_result(self, job: EvalJob) -> dict[str, Any]:
        """Return EnvArena's aggregate result for a platform-neutral evaluation job."""

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

    def get_trajs(self, job: EvalJob, include_messages: bool = False) -> list[Traj]:
        job_id = self._job_id(job)
        tasks = {str(task.get("task_id")): task for task in self.list_job_tasks(job_id)}
        model_ref = job.get("model_ref", {})
        model_name = str(model_ref.get("model_name") or model_ref.get("runtime_model_name") or "")
        source = str(job.get("metadata", {}).get("source", "eval"))
        trajs: list[Traj] = []
        for trajectory in self.list_job_trajectories(job_id, inline=include_messages):
            task_id = str(trajectory.get("task_id", ""))
            task, attempt = tasks.get(task_id, {}), trajectory.get("latest_attempt")
            traj: Traj = {
                "traj_id": f"{job_id}:{task_id}:{attempt or 0}",
                "task_id": task_id,
                "model_ref": model_name,
                "harness_id": job.get("harness_ref", {}).get("key"),
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
    def _job_id(job: EvalJob) -> str:
        job_id = str(job.get("eval_job_id") or job.get("job_id") or "")
        if not job_id:
            raise EnvArenaConfigurationError("EvalJob has no eval_job_id/job_id.")
        return job_id
