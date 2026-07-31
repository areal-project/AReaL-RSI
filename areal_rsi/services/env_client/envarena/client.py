"""EnvArena contract, configuration, and generated-SDK-backed adapter."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import yaml

from areal_rsi.schemas.core import EvalJob, EvalRequest, Traj

from ..base import EnvClient

from .generated.api.jobs import post_openapi_v1_jobs
from .generated.client import AuthenticatedClient
from .generated.models.concurrency_policy import ConcurrencyPolicy
from .generated.models.create_job_request import CreateJobRequest
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

    def __post_init__(self) -> None:
        if not self.control_base_url.strip():
            raise EnvArenaConfigurationError("envarena.control_base_url is required.")
        if not self.api_token.strip():
            raise EnvArenaConfigurationError("EnvArena API token is required.")
        if self.timeout_seconds <= 0:
            raise EnvArenaConfigurationError("timeout_seconds must be positive.")
        if not self.model_instance_name_prefix.strip():
            raise EnvArenaConfigurationError("model_instance_name_prefix must be non-empty.")

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
        """List model routes configured in EnvArena.

        ``disable_stream`` excludes dynamically registered stream model names,
        which keeps the result suitable for selecting a stable RSI model route.
        """

        return list(
            self._request_json(
                "GET",
                "/openapi/v1/llm/models",
                params={"disable-stream": str(disable_stream).lower()},
                operation="list llm models",
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
                "runtime_model_name": inline_model_name or registry_model.key,
                **({"registry_ref": registry_model.to_dict()} if registry_model is not None else {}),
            },
            "task_set": benchmark.key,
            "benchmark_ref": benchmark.to_dict(),
            "harness_ref": harness.to_dict(),
            "harness_config": {"harness_ref": harness.to_dict()},
            "task_ids": list(payload.get("task_ids", [])),
            "assets": dict(assets or {}),
            "job_config": dict(job_config or {}),
            "metadata": dict(metadata or {}),
            "raw": payload,
        }

    def submit_eval(self, request: EvalRequest) -> EvalJob:
        job_config = dict(request.job_config)
        skill_ref = self._resolve_optional_registry_asset("skills", request.skill)
        reward_ref = self._resolve_optional_registry_asset("rewards", request.reward)
        dataset_ref = self._resolve_dataset_asset(request.dataset, reward_ref=reward_ref)
        harness_ref = self._resolve_harness_asset(request.harness, skill_ref=skill_ref)
        model = self._resolve_model_asset(request.model)
        return self._submit_benchmark_job(
            benchmark_ref=dataset_ref,
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
                "dataset": dataset_ref.to_dict(),
                "harness": harness_ref.to_dict(),
                **({"skill": skill_ref.to_dict()} if skill_ref is not None else {}),
                **({"reward": reward_ref.to_dict()} if reward_ref is not None else {}),
            },
            job_config=job_config,
        )

    def _resolve_optional_registry_asset(
        self, collection: str, config: Mapping[str, Any] | None
    ) -> EnvArenaVersionRef | None:
        if config is None:
            return None
        asset = self._asset_config(config, collection.removesuffix("s"))
        if asset["type"] == "ref":
            return self._registry_ref_from_asset(asset, collection)
        return self._create_registry_asset(collection, asset)

    def _resolve_dataset_asset(
        self, config: Mapping[str, Any], *, reward_ref: EnvArenaVersionRef | None
    ) -> EnvArenaVersionRef:
        asset = self._asset_config(config, "dataset")
        if asset["type"] == "ref":
            return self._registry_ref_from_asset(asset, "benchmarks")
        spec = self._asset_spec(asset, "dataset")
        if reward_ref is not None and "reward_ref" not in spec:
            spec["reward_ref"] = reward_ref.to_dict()
        return self._create_registry_asset("benchmarks", asset, spec=spec)

    def _resolve_harness_asset(
        self, config: Mapping[str, Any], *, skill_ref: EnvArenaVersionRef | None
    ) -> EnvArenaVersionRef:
        asset = self._asset_config(config, "harness")
        if asset["type"] == "ref":
            return self._registry_ref_from_asset(asset, "harnesses")
        spec = self._asset_spec(asset, "harness")
        if skill_ref is not None and "skill_refs" not in spec:
            spec["skill_refs"] = [skill_ref.to_dict()]
        return self._create_registry_asset("harnesses", asset, spec=spec)

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
