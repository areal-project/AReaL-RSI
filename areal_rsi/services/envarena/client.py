"""EnvArena contract, configuration, and generated-SDK-backed adapter."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml

from areal_rsi.schemas.core import Endpoint, EvalJob, EvalRequest, ModelRef, Traj, TrajSource

from ..env_client.base import EnvClient

from .generated.api.jobs import post_openapi_v1_jobs
from .generated.api.llm import post_openapi_v1_llm_models
from .generated.client import AuthenticatedClient
from .generated.models.concurrency_policy import ConcurrencyPolicy
from .generated.models.create_job_request import CreateJobRequest
from .generated.models.create_llm_model_request import CreateLLMModelRequest
from .generated.models.llm_model_endpoint_request import LLMModelEndpointRequest
from .generated.models.llm_model_endpoint_request_inbound_protos_item import (
    LLMModelEndpointRequestInboundProtosItem,
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
    gateway_model_alias_prefix: str = "areal-rsi-"
    gateway_inbound_protos: tuple[str, ...] = ("chat", "responses")

    def __post_init__(self) -> None:
        if not self.control_base_url.strip():
            raise EnvArenaConfigurationError("envarena.control_base_url is required.")
        if not self.api_token.strip():
            raise EnvArenaConfigurationError("EnvArena API token is required.")
        if self.timeout_seconds <= 0:
            raise EnvArenaConfigurationError("timeout_seconds must be positive.")
        allowed = {"chat", "responses", "messages"}
        if not self.gateway_inbound_protos or set(self.gateway_inbound_protos) - allowed:
            raise EnvArenaConfigurationError(
                "gateway.inbound_protos must contain one or more of: chat, responses, messages."
            )

    @property
    def sdk_base_url(self) -> str:
        """Return the base before the generated SDK's hard-coded `/openapi/v1` path."""

        base_url = self.control_base_url.rstrip("/")
        suffix = "/openapi/v1"
        return base_url[: -len(suffix)] if base_url.endswith(suffix) else base_url

    def gateway_alias_for(self, model_ref: str) -> str:
        if not model_ref.strip():
            raise EnvArenaConfigurationError("AReaL endpoint must provide a non-empty model_ref.")
        return f"{self.gateway_model_alias_prefix}{model_ref}"

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
        gateway = section.get("gateway", {})
        if not isinstance(gateway, Mapping):
            raise EnvArenaConfigurationError("gateway must be a YAML mapping.")
        return cls(
            control_base_url=control_base_url,
            api_token=token,
            timeout_seconds=float(section.get("timeout_seconds", 30)),
            verify_ssl=bool(section.get("verify_ssl", True)),
            gateway_model_alias_prefix=str(gateway.get("model_alias_prefix", "areal-rsi-")),
            gateway_inbound_protos=tuple(str(item) for item in gateway.get("inbound_protos", ["chat", "responses"])),
        )


class EnvArenaError(RuntimeError):
    """Base error raised by the real EnvArena adapter."""


class EnvArenaHTTPError(EnvArenaError):
    """An EnvArena control-plane response outside the operation's success codes."""

    def __init__(self, operation: str, status_code: int, detail: Any):
        self.operation, self.status_code, self.detail = operation, status_code, detail
        super().__init__(f"EnvArena {operation} failed with HTTP {status_code}: {detail}")


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

    def list_models(self, *, disable_stream: bool = True) -> list[dict[str, Any]]:
        """List model routes configured in the EnvArena gateway.

        ``disable_stream`` excludes dynamically registered stream model names,
        which keeps the result suitable for selecting a stable RSI model route.
        """

        return list(
            self._request_json(
                "GET",
                "/openapi/v1/llm/models",
                params={"disable-stream": str(disable_stream).lower()},
                operation="list gateway models",
            ).get("items", [])
        )

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

    def register_model(self, endpoint: Endpoint) -> ModelRef:
        return self._upsert_gateway_model(endpoint)

    def update_model(self, model_ref: ModelRef, endpoint: Endpoint) -> ModelRef:
        alias = str(model_ref.get("gateway_alias") or model_ref.get("model_name") or "")
        if not alias:
            raise EnvArenaConfigurationError("model_ref has no gateway_alias or model_name.")
        return self._upsert_gateway_model(endpoint, alias=alias)

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
    ) -> EvalJob:
        benchmark, harness = self._as_version_ref(benchmark_ref), self._as_version_ref(harness_ref)
        registry_model = self._as_version_ref(model_ref) if model_ref is not None else None
        inline_model_name = (model_name or "").strip()
        if not inline_model_name and registry_model is None:
            raise EnvArenaConfigurationError(
                "BenchmarkJob submission requires a gateway model_name or a published model_ref."
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
        policy = None
        if concurrency_policy is not None:
            policy = ConcurrencyPolicy(
                max_parallel=int(concurrency_policy.get("max_parallel", 1)),
                max_retries=int(concurrency_policy.get("max_retries", 0)),
                retry_backoff_seconds=int(concurrency_policy.get("retry_backoff_seconds", 30)),
            )
        body = CreateJobRequest(
            benchmark_ref=VersionRef(**benchmark.to_dict()),
            harness_ref=VersionRef(**harness.to_dict()),
            model_ref=VersionRef(**registry_model.to_dict()) if registry_model is not None else UNSET,
            model=model,
            job_id=job_id if job_id else UNSET,
            sample_numbers=sample_numbers,
            concurrency_policy=policy if policy is not None else UNSET,
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
                "gateway_alias": inline_model_name or registry_model.key,
                **({"registry_ref": registry_model.to_dict()} if registry_model is not None else {}),
            },
            "task_set": benchmark.key,
            "benchmark_ref": benchmark.to_dict(),
            "harness_ref": harness.to_dict(),
            "harness_config": {"harness_ref": harness.to_dict()},
            "task_ids": list(payload.get("task_ids", [])),
            "metadata": dict(metadata or {}),
            "raw": payload,
        }

    def submit_eval(self, request: EvalRequest) -> EvalJob:
        harness_config = request.harness_config
        harness_ref = harness_config.get("harness_ref")
        if harness_ref is None:
            raise EnvArenaConfigurationError(
                "submit_eval requires harness_config['harness_ref'] with a published key and version."
            )
        registry_ref = request.model_ref.get("registry_ref")
        model_name = str(request.model_ref.get("gateway_alias") or request.model_ref.get("model_name") or "")
        return self._submit_benchmark_job(
            benchmark_ref=self._as_version_ref(request.task_set),
            harness_ref=harness_ref,
            model_name=model_name or None,
            model_ref=registry_ref,
            job_id=harness_config.get("job_id"),
            sample_numbers=int(harness_config.get("sample_numbers", 1)),
            concurrency_policy=harness_config.get("concurrency_policy"),
            tags=list(harness_config.get("tags", [])),
            metadata=request.metadata,
            model_parameters=harness_config.get("model_parameters"),
            model_env=harness_config.get("model_env"),
        )

    def get_job_summary(self, job_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/openapi/v1/jobs/{job_id}/summary", operation="get job summary")

    def get_job_result(self, job_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/openapi/v1/jobs/{job_id}/result", operation="get job result")

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
        job_id = str(job.get("eval_job_id") or job.get("job_id") or "")
        if not job_id:
            raise EnvArenaConfigurationError("EvalJob has no eval_job_id/job_id.")
        tasks = {str(task.get("task_id")): task for task in self.list_job_tasks(job_id)}
        model_ref = job.get("model_ref", {})
        model_name = str(model_ref.get("model_name") or model_ref.get("gateway_alias") or "")
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

    def submit_rollout(
        self,
        model_ref: ModelRef,
        tasks: list[dict[str, Any]],
        harness_config: dict[str, Any],
        source: TrajSource,
        metadata: dict[str, Any] | None = None,
    ) -> EvalJob:
        raise NotImplementedError(
            "EnvArena Control Plane has no arbitrary offline task rollout endpoint. "
            "Use submit_eval with a published Benchmark or implement online Stream support separately."
        )

    def _upsert_gateway_model(self, endpoint: Endpoint, *, alias: str | None = None) -> ModelRef:
        model_ref = str(endpoint.get("model_ref", ""))
        model_name = alias or str(endpoint.get("gateway_alias") or self.config.gateway_alias_for(model_ref))
        base_url, upstream_model = str(endpoint.get("base_url", "")), str(endpoint.get("upstream_model") or model_ref)
        if not base_url or not upstream_model:
            raise EnvArenaConfigurationError("Endpoint must include non-empty base_url and model_ref/upstream_model.")
        try:
            protocols = [
                LLMModelEndpointRequestInboundProtosItem(str(item))
                for item in endpoint.get("inbound_protos", self.config.gateway_inbound_protos)
            ]
        except ValueError as exc:
            raise EnvArenaConfigurationError("Unsupported gateway inbound protocol in endpoint configuration.") from exc
        request_endpoint = LLMModelEndpointRequest(
            upstream_model=upstream_model,
            base_url=base_url,
            inbound_protos=protocols,
            api_key=str(endpoint["api_key"]) if endpoint.get("api_key") else UNSET,
            endpoint_id=str(endpoint["endpoint_id"]) if endpoint.get("endpoint_id") else UNSET,
            weight=float(endpoint.get("weight", 1)),
            enabled=bool(endpoint.get("enabled", True)),
        )
        request = CreateLLMModelRequest(model_name=model_name, endpoints=[request_endpoint])
        if isinstance(endpoint.get("metadata"), Mapping):
            request.additional_properties["metadata"] = self._string_metadata(endpoint["metadata"])
        payload = self._unwrap_generated(
            post_openapi_v1_llm_models.sync_detailed(client=self._client, body=request),
            "upsert gateway model",
            {201},
        )
        return {
            "model_name": str(payload.get("model_name", model_name)),
            "gateway_alias": str(payload.get("model_name", model_name)),
            "endpoint_ids": [item.get("endpoint_id") for item in payload.get("endpoints", [])],
            "metadata": payload.get("metadata", {}),
        }

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
        try:
            response = self._client.get_httpx_client().request(method, path, params=params, json=json_body)
        except httpx.HTTPError as exc:
            raise EnvArenaError(f"EnvArena {operation} transport error: {exc}") from exc
        payload = self._decode_json(response, operation)
        if response.status_code not in (expected_statuses or {200}):
            raise EnvArenaHTTPError(operation, response.status_code, payload)
        if not isinstance(payload, dict):
            raise EnvArenaError(f"EnvArena {operation} returned a non-object JSON payload.")
        return payload

    @staticmethod
    def _decode_json(response: httpx.Response, operation: str) -> Any:
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
