import json

import httpx
import pytest

from areal_rsi.schemas import EvalRequest
from areal_rsi.services import (
    EnvArenaConfig,
    EnvArenaConfigurationError,
    EnvArenaError,
    EnvArenaClient,
    EnvArenaHTTPError,
)


def _client(handler, *, job_config: dict | None = None) -> EnvArenaClient:
    return EnvArenaClient(
        EnvArenaConfig(
            control_base_url="https://arena.example.test/envarena/openapi/v1",
            api_token="test-token",
            model_instance_name_prefix="areal-rsi-",
            job_config=job_config or {},
        ),
        transport=httpx.MockTransport(handler),
    )


def _model_service_instance(instance_id: str, *, status: str = "PENDING") -> dict[str, object]:
    return {
        "instance_id": instance_id,
        "service_id": "svc-1",
        "model_path": "/models/model-a",
        "resource_spec": {"gpu_num": 1},
        "sglang": {},
        "node_num": 1,
        "status": status,
        "created_at": "2026-08-03T00:00:00Z",
        "updated_at": "2026-08-03T00:00:00Z",
        "submit_attempts": 1,
    }


def _benchmark_job_payload(job_id: str, *, harness_key: str = "base-harness") -> dict[str, object]:
    return {
        "job_id": job_id,
        "harness_key": harness_key,
        "env_keys": ["env-a"],
        "concurrency": 1,
        "sample_numbers": 1,
        "status": "PENDING",
        "created_at": "2026-08-03T00:00:00Z",
    }


def _model_version_payload(
    key: str,
    version: str,
    *,
    model_name: str = "ckpt-alias",
    parameters: dict | None = None,
    env: dict | None = None,
    status: str = "PUBLISHED",
) -> dict[str, object]:
    spec: dict[str, object] = {"model_name": model_name}
    if parameters is not None:
        spec["parameters"] = parameters
    if env is not None:
        spec["env"] = env
    payload = {
        "key": key,
        "version": version,
        "status": status,
        "created_at": "2026-08-03T00:00:00Z",
        "spec": spec,
    }
    if status == "PUBLISHED":
        payload["published_at"] = "2026-08-03T00:00:00Z"
    return payload


def test_config_loads_environment_values_and_normalizes_generated_sdk_base_url(tmp_path):
    config_path = tmp_path / "envarena.yaml"
    config_path.write_text(
        """
envarena:
  control_base_url_env: ARENA_CONTROL_URL
  token_env: ARENA_TOKEN
  job_config:
    sample_numbers: 2
    concurrency_policy:
      max_parallel: 8
      max_retries: 1
      retry_backoff_seconds: 10
    tags: [default]
""",
        encoding="utf-8",
    )

    config = EnvArenaConfig.from_yaml(
        config_path,
        environ={
            "ARENA_CONTROL_URL": "https://arena.example.test/envarena/openapi/v1",
            "ARENA_TOKEN": "secret",
        },
    )

    assert config.sdk_base_url == "https://arena.example.test/envarena"
    assert config.api_token == "secret"
    assert config.model_instance_name_prefix == "areal-rsi-"
    assert config.job_config == {
        "sample_numbers": 2,
        "concurrency_policy": {
            "max_parallel": 8,
            "max_retries": 1,
            "retry_backoff_seconds": 10,
        },
        "tags": ["default"],
    }


def test_eval_with_model_path_starts_mock_model_instance_and_collects_results():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["Authorization"] == "Bearer test-token"
        path = request.url.path
        if request.method == "GET" and path == "/envarena/openapi/v1/benchmarks":
            assert request.url.params["limit"] == "200"
            return httpx.Response(200, json={"items": [{"key": "swe_verified", "name": "SWE"}], "total": 1})
        if request.method == "GET" and path == "/envarena/openapi/v1/benchmarks/swe_verified/versions":
            assert request.url.params["status"] == "PUBLISHED"
            return httpx.Response(200, json={"items": [{"key": "swe_verified", "version": "1.0.0"}]})
        if request.method == "POST" and path == "/envarena/openapi/v1/jobs":
            payload = json.loads(request.content)
            assert payload["benchmark_ref"] == {"key": "swe_verified", "version": "1.0.0"}
            assert payload["harness_ref"] == {"key": "swe-harness", "version": "1.0.0"}
            assert payload["model"]["model_name"] == "areal-rsi-ckpt_001"
            return httpx.Response(202, json={"job_id": "job_001", "task_ids": ["task_001"]})
        if request.method == "GET" and path == "/envarena/openapi/v1/jobs/job_001/summary":
            return httpx.Response(200, json={"job": {"job_id": "job_001", "status": "RUNNING"}, "task_counts": {"total": 1, "done": 0}})
        if request.method == "GET" and path == "/envarena/openapi/v1/jobs/job_001/result":
            return httpx.Response(200, json={"job_id": "job_001", "aggregate_score": 0.8})
        if request.method == "GET" and path == "/envarena/openapi/v1/jobs/job_001/tasks":
            return httpx.Response(
                200,
                json={"items": [{"task_id": "task_001", "score": 0.8, "status": "DONE"}], "total": 1},
            )
        if request.method == "GET" and path == "/envarena/openapi/v1/jobs/job_001/trajectories":
            assert request.url.params["inline"] == "true"
            return httpx.Response(
                200,
                json={
                    "job_id": "job_001",
                    "tasks": [
                        {
                            "task_id": "task_001",
                            "latest_attempt": 1,
                            "trajectory_uri": "oss://arena/job_001/task_001/trajectory.jsonl",
                            "event_count": 1,
                            "events": [{"type": "message", "role": "assistant", "content": "patch"}],
                        }
                    ],
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = _client(handler)
    assert client.list_benchmarks() == [{"key": "swe_verified", "name": "SWE"}]
    assert client.list_benchmark_versions("swe_verified") == [{"key": "swe_verified", "version": "1.0.0"}]

    job = client.submit_eval(EvalRequest(
        model={
            "type": "new",
            "model_path": "ckpt_001",
        },
        benchmark="swe_verified@1.0.0",
        harness={"key": "swe-harness", "version": "1.0.0"},
        metadata={"run_id": "run_001", "source": "eval"},
    ))

    assert job["eval_job_id"] == "job_001"
    assert client.get_eval_progress(job)["job"]["status"] == "RUNNING"
    assert client.get_eval_result(job)["aggregate_score"] == 0.8
    trajs = client.get_trajs(job, include_messages=True)
    assert trajs == [
        {
            "traj_id": "job_001:task_001:1",
            "task_id": "task_001",
            "model_ref": "areal-rsi-ckpt_001",
            "harness_id": "swe-harness",
            "reward": 0.8,
            "status": "DONE",
            "metadata": {
                "source": "eval",
                "eval_job_id": "job_001",
                "trajectory_uri": "oss://arena/job_001/task_001/trajectory.jsonl",
                "attempt": 1,
                "event_count": 1,
            },
            "raw": {
                "task": {"task_id": "task_001", "score": 0.8, "status": "DONE"},
                "trajectory": {
                    "task_id": "task_001",
                    "latest_attempt": 1,
                    "trajectory_uri": "oss://arena/job_001/task_001/trajectory.jsonl",
                    "event_count": 1,
                    "events": [{"type": "message", "role": "assistant", "content": "patch"}],
                },
            },
            "messages": [{"type": "message", "role": "assistant", "content": "patch"}],
        }
    ]
    assert job["assets"]["model"] == {
        "model_name": "areal-rsi-ckpt_001",
        "model_path": "ckpt_001",
        "status": "mock_started",
        "backend": "mock_envarena_model_instance",
    }
    assert len(seen) == 7


def test_submit_eval_requires_explicit_published_harness_and_benchmark_refs():
    client = _client(lambda request: pytest.fail(f"Unexpected request: {request.url}"))

    with pytest.raises(EnvArenaConfigurationError, match="harness refs require"):
        client.submit_eval(EvalRequest(
            model={"type": "ref", "model_name": "model"},
            benchmark="benchmark@1.0.0",
            harness={},
        ))

    with pytest.raises(EnvArenaConfigurationError, match="benchmark must be an existing Arena ref"):
        client.submit_eval(EvalRequest(
            model={"type": "ref", "model_name": "model"},
            benchmark={"type": "new", "key": "benchmark", "version": "1.0.0", "spec": {}},
            harness={"key": "harness", "version": "1.0.0"},
        ))

    with pytest.raises(EnvArenaConfigurationError, match="harness must be an existing Arena ref"):
        client.submit_eval(EvalRequest(
            model={"type": "ref", "model_name": "model"},
            benchmark="benchmark@1.0.0",
            harness={"type": "new", "key": "harness", "version": "1.0.0", "spec": {}},
        ))


def test_http_errors_are_exposed_with_status_code():
    client = _client(lambda request: httpx.Response(403, json={"error": "forbidden"}))

    with pytest.raises(EnvArenaHTTPError) as exc_info:
        client.list_skills()

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == {"error": "forbidden"}


def test_llm_model_list_can_exclude_dynamic_stream_routes():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/envarena/openapi/v1/llm/models"
        assert request.url.params == httpx.QueryParams({"disable-stream": "true"})
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "model_name": "areal-rsi-ckpt_001",
                        "enabled": True,
                        "endpoints": [
                            {
                                "endpoint_id": "ep-1",
                                "upstream_model": "areal-rsi-ckpt_001",
                                "base_url": "https://llm.example.test",
                                "inbound_protos": ["chat"],
                                "weight": 1,
                                "enabled": True,
                                "upstream_extra_params": {},
                            }
                        ],
                    }
                ]
            },
        )

    assert _client(handler).list_models() == [
        {
            "model_name": "areal-rsi-ckpt_001",
            "enabled": True,
            "endpoints": [
                {
                    "endpoint_id": "ep-1",
                    "upstream_model": "areal-rsi-ckpt_001",
                    "base_url": "https://llm.example.test",
                    "inbound_protos": ["chat"],
                    "weight": 1,
                    "enabled": True,
                    "upstream_extra_params": {},
                }
            ],
        }
    ]


def test_online_harnesses_use_generated_get_api():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/envarena/openapi/v1/online/harnesses"
        return httpx.Response(200, json={"harness": ["claude-code@1.0.0"]})

    assert _client(handler).list_online_harnesses() == ["claude-code@1.0.0"]


def test_registry_entity_page_fetches_one_page_without_auto_paging():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET"
        assert request.url.path == "/envarena/openapi/v1/envs"
        assert request.url.params == httpx.QueryParams({"limit": "1", "offset": "0"})
        return httpx.Response(200, json={"items": [{"key": "env-a"}], "total": 2, "limit": 1, "offset": 0})

    assert _client(handler).list_registry_entity_page("env", limit=1) == {
        "items": [{"key": "env-a"}],
        "total": 2,
        "limit": 1,
        "offset": 0,
    }
    assert len(seen) == 1


def test_registry_and_task_pages_are_fully_collected_and_task_attempt_is_requested():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["Authorization"] == "Bearer test-token"
        if request.url.path == "/envarena/openapi/v1/skills":
            offset = request.url.params["offset"]
            if offset == "0":
                return httpx.Response(200, json={"items": [{"key": "skill-1"}], "total": 2})
            assert offset == "1"
            return httpx.Response(200, json={"items": [{"key": "skill-2"}], "total": 2})
        if request.url.path == "/envarena/openapi/v1/jobs/job_002/tasks":
            assert request.url.params["status"] == "DONE"
            offset = request.url.params["offset"]
            if offset == "0":
                return httpx.Response(200, json={"items": [{"task_id": "task-1"}], "total": 2})
            assert offset == "1"
            return httpx.Response(200, json={"items": [{"task_id": "task-2"}], "total": 2})
        if request.url.path == "/envarena/openapi/v1/jobs/job_002/tasks/task-2/trajectory":
            assert request.url.params == httpx.QueryParams({"format": "json", "attempt": "3"})
            return httpx.Response(200, json={"task_id": "task-2", "attempt": 3, "events": []})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = _client(handler)

    assert client.list_skills() == [{"key": "skill-1"}, {"key": "skill-2"}]
    assert client.list_job_tasks("job_002", status="DONE") == [{"task_id": "task-1"}, {"task_id": "task-2"}]
    assert client.get_task_trajectory("job_002", "task-2", attempt=3) == {
        "task_id": "task-2",
        "attempt": 3,
        "events": [],
    }
    assert len(seen) == 5


def test_submit_eval_can_use_existing_runtime_model_and_encode_runtime_config():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/envarena/openapi/v1/jobs":
            payload = json.loads(request.content)
            assert payload == {
                "benchmark_ref": {"key": "benchmark", "version": "2.0.0"},
                "harness_ref": {"key": "harness", "version": "1.2.0"},
                "model": {
                    "model_name": "stable-alias",
                    "parameters": {"temperature": 0.2},
                    "env": {"MODEL_SERVICE_URL": "https://model.example.test"},
                },
                "job_id": "run-42",
                "sample_numbers": 3,
                "concurrency_policy": {
                    "max_parallel": 4,
                    "max_retries": 2,
                    "retry_backoff_seconds": 5,
                },
                "tags": ["rsi", "holdout"],
                "metadata": {"run_id": "42", "priority": "7"},
            }
            return httpx.Response(202, json={"job_id": "run-42-260729", "task_ids": ["task-1", "task-2"]})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = _client(handler)
    job = client.submit_eval(EvalRequest(
        model={"type": "ref", "model_name": "stable-alias"},
        benchmark="benchmark@2.0.0",
        harness={"key": "harness", "version": "1.2.0"},
        job_config={
            "job_id": "run-42",
            "sample_numbers": 3,
            "concurrency_policy": {"max_parallel": 4, "max_retries": 2, "retry_backoff_seconds": 5},
            "tags": ["rsi", "holdout"],
            "model_parameters": {"temperature": 0.2},
            "model_env": {"MODEL_SERVICE_URL": "https://model.example.test"},
        },
        metadata={"run_id": 42, "priority": 7},
    ))

    assert job["eval_job_id"] == "run-42-260729"
    assert job["task_ids"] == ["task-1", "task-2"]
    assert job["status"] == "PENDING"


def test_submit_eval_merges_default_job_config_from_client_config():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/envarena/openapi/v1/jobs":
            payload = json.loads(request.content)
            assert payload["sample_numbers"] == 5
            assert payload["concurrency_policy"] == {
                "max_parallel": 8,
                "max_retries": 2,
                "retry_backoff_seconds": 30,
            }
            assert payload["tags"] == ["request"]
            assert payload["model"]["parameters"] == {"temperature": 0.2, "top_p": 0.9}
            return httpx.Response(202, json={"job_id": "job-defaults", "task_ids": []})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = _client(
        handler,
        job_config={
            "sample_numbers": 5,
            "concurrency_policy": {
                "max_parallel": 8,
                "max_retries": 0,
                "retry_backoff_seconds": 30,
            },
            "tags": ["default"],
            "model_parameters": {"temperature": 0.2},
        },
    )
    job = client.submit_eval(EvalRequest(
        model={"type": "ref", "model_name": "stable-alias"},
        benchmark="benchmark@2.0.0",
        harness="harness@1.2.0",
        job_config={
            "concurrency_policy": {"max_retries": 2},
            "tags": ["request"],
            "model_parameters": {"top_p": 0.9},
        },
    ))

    assert job["eval_job_id"] == "job-defaults"
    assert job["job_config"]["concurrency_policy"] == {
        "max_parallel": 8,
        "max_retries": 2,
        "retry_backoff_seconds": 30,
    }


def test_submit_eval_can_reference_a_published_model_without_inline_secrets():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/envarena/openapi/v1/jobs"
        payload = json.loads(request.content)
        assert payload["model_ref"] == {"key": "DeepSeek-V4-Pro", "version": "1.0.2"}
        assert "model" not in payload
        return httpx.Response(202, json={"job_id": "registry-model-smoke", "task_ids": ["task-1"]})

    job = _client(handler).submit_eval(EvalRequest(
        model={"type": "ref", "registry_ref": {"key": "DeepSeek-V4-Pro", "version": "1.0.2"}},
        benchmark="benchmark@2.0.0",
        harness="harness@1.2.0",
    ))

    assert job["model_ref"] == {
        "model_name": "DeepSeek-V4-Pro",
        "runtime_model_name": "DeepSeek-V4-Pro",
        "registry_ref": {"key": "DeepSeek-V4-Pro", "version": "1.0.2"},
    }


def test_submit_eval_can_create_new_skill_and_use_existing_benchmark_and_harness_refs():
    seen: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content or b"{}")
        seen.append((request.method, request.url.path, payload))
        path = request.url.path
        if path == "/envarena/openapi/v1/skills":
            assert payload["key"] == "skill-rsi"
            return httpx.Response(201, json={})
        if path == "/envarena/openapi/v1/skills/skill-rsi/versions":
            assert payload == {"version": "1.0.0", "spec": {"manifest": {"name": "skill-rsi"}}}
            return httpx.Response(201, json={})
        if path == "/envarena/openapi/v1/skills/skill-rsi/versions/1.0.0/publish":
            return httpx.Response(200, json={})
        if path == "/envarena/openapi/v1/jobs":
            assert payload["benchmark_ref"] == {"key": "benchmark-rsi", "version": "1.0.0"}
            assert payload["harness_ref"] == {"key": "harness-rsi", "version": "1.0.0"}
            assert payload["model"] == {"model_name": "stable-alias"}
            assert "skill_ref" not in payload
            return httpx.Response(202, json={"job_id": "job-created-assets", "task_ids": []})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    job = _client(handler).submit_eval(EvalRequest(
        model={"type": "ref", "model_name": "stable-alias"},
        benchmark={"key": "benchmark-rsi", "version": "1.0.0"},
        harness={"key": "harness-rsi", "version": "1.0.0"},
        skill={
            "type": "new",
            "key": "skill-rsi",
            "version": "1.0.0",
            "name": "RSI Skill",
            "spec": {"manifest": {"name": "skill-rsi"}},
        },
    ))

    assert job["eval_job_id"] == "job-created-assets"
    assert job["assets"]["benchmark"] == {"key": "benchmark-rsi", "version": "1.0.0"}
    assert job["assets"]["skill"] == {"key": "skill-rsi", "version": "1.0.0"}
    assert "reward" not in job["assets"]
    assert len(seen) == 4


def test_get_trajs_omits_messages_without_inline_events():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/envarena/openapi/v1/jobs/job_003/tasks":
            return httpx.Response(200, json={"items": [{"task_id": "task-3", "status": "DONE", "score": 1.0}], "total": 1})
        if request.url.path == "/envarena/openapi/v1/jobs/job_003/trajectories":
            assert request.url.params["inline"] == "false"
            return httpx.Response(
                200,
                json={"tasks": [{"task_id": "task-3", "latest_attempt": 1, "trajectory_uri": "oss://trajectory"}]},
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = _client(handler)
    trajs = client.get_trajs(
        {
            "eval_job_id": "job_003",
            "model_ref": {"model_name": "stable-alias"},
            "harness_ref": {"key": "harness"},
        }
    )

    assert trajs[0]["reward"] == 1.0
    assert "messages" not in trajs[0]


def test_non_json_and_transport_failures_are_normalized():
    non_json_client = _client(lambda request: httpx.Response(502, content=b"upstream unavailable"))

    with pytest.raises(EnvArenaHTTPError) as non_json_error:
        non_json_client.list_harnesses()

    assert non_json_error.value.status_code == 502
    assert non_json_error.value.detail == {
        "error": "non-JSON response for list harnesses",
        "body": "upstream unavailable",
    }

    def broken_transport(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    transport_client = _client(broken_transport)
    with pytest.raises(EnvArenaError, match="transport error"):
        transport_client.list_benchmarks()


def test_registry_crud_helpers_use_consistent_asset_paths_and_payloads():
    seen: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content or b"{}")
        seen.append((request.method, request.url.path, payload))
        path = request.url.path
        if request.method == "GET" and path == "/envarena/openapi/v1/envs":
            assert request.url.params["limit"] == "1"
            assert request.url.params["offset"] == "0"
            return httpx.Response(200, json={"items": [{"key": "env-a"}], "total": 1})
        if request.method == "POST" and path == "/envarena/openapi/v1/envs":
            assert payload == {"key": "env-a", "name": "Env A", "tags": ["rsi"], "metadata": {"owner": "7"}}
            return httpx.Response(201, json={"key": "env-a"})
        if request.method == "GET" and path == "/envarena/openapi/v1/envs/env-a":
            return httpx.Response(200, json={"key": "env-a", "name": "Env A"})
        if request.method == "PATCH" and path == "/envarena/openapi/v1/envs/env-a":
            assert payload == {"description": "updated"}
            return httpx.Response(200, json={"key": "env-a", "description": "updated"})
        if request.method == "POST" and path == "/envarena/openapi/v1/envs/env-a/versions":
            assert payload == {"version": "1.0.0", "spec": {"query": {"uri": "oss://q"}}, "supersedes": "0.9.0"}
            return httpx.Response(201, json={"key": "env-a", "version": "1.0.0"})
        if request.method == "POST" and path == "/envarena/openapi/v1/envs/env-a/versions/1.0.0/publish":
            return httpx.Response(200, json={"key": "env-a", "version": "1.0.0", "status": "PUBLISHED"})
        if request.method == "DELETE" and path == "/envarena/openapi/v1/envs/env-a/versions/1.0.0":
            return httpx.Response(204)
        if request.method == "DELETE" and path == "/envarena/openapi/v1/envs/env-a":
            return httpx.Response(204)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = _client(handler)

    assert client.list_registry_entities("env", limit=1) == [{"key": "env-a"}]
    assert client.create_env(key="env-a", name="Env A", tags=["rsi"], metadata={"owner": 7})["key"] == "env-a"
    assert client.get_env("env-a")["name"] == "Env A"
    assert client.update_env("env-a", description="updated")["description"] == "updated"
    assert client.create_env_version("env-a", version="1.0.0", spec={"query": {"uri": "oss://q"}}, supersedes="0.9.0")[
        "version"
    ] == "1.0.0"
    assert client.publish_env_version("env-a", "1.0.0")["status"] == "PUBLISHED"
    assert client.delete_registry_version("env", "env-a", "1.0.0") == {}
    assert client.delete_env("env-a") == {}
    assert [method for method, _, _ in seen] == ["GET", "POST", "GET", "PATCH", "POST", "POST", "DELETE", "DELETE"]


def test_model_service_can_be_created_from_checkpoint_when_enabled():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/envarena/openapi/v1/model-services"
        payload = json.loads(request.content)
        assert payload == {
            "served_model_name": "ckpt-alias",
            "model_path": "/models/ckpt",
            "replicas": 2,
            "readiness_policy": "all",
            "resource_spec": {"gpu_num": 1},
            "sglang": {"tp": 1},
            "metadata": {"run_id": "run-1", "source": "areal-rsi"},
        }
        return httpx.Response(
            201,
            json={
                "service_id": "svc-1",
                "served_model_name": "ckpt-alias",
                "status": "PENDING",
                "replicas": 2,
                "desired_replicas": 2,
                "readiness_policy": "all",
                "gateway": {},
                "ready_replicas": 0,
                "instances": [],
                "created_at": "2026-08-03T00:00:00Z",
                "updated_at": "2026-08-03T00:00:00Z",
            },
        )

    client = EnvArenaClient(
        EnvArenaConfig(
            control_base_url="https://arena.example.test/envarena/openapi/v1",
            api_token="test-token",
            model_service_config={
                "enabled": True,
                "replicas": 2,
                "readiness_policy": "all",
                "resource_spec": {"gpu_num": 1},
                "sglang": {"tp": 1},
            },
        ),
        transport=httpx.MockTransport(handler),
    )

    model = client.ensure_model_service_for_ckpt(
        {"model_path": "/models/ckpt", "served_model_name": "ckpt-alias"},
        metadata={"run_id": "run-1"},
    )

    assert model["model_name"] == "ckpt-alias"
    assert model["asset"]["model_service"]["service_id"] == "svc-1"


def test_create_model_service_merges_base_model_catalog_config_with_overrides():
    seen: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content or b"{}")
        seen.append((request.method, request.url.path, payload))
        if request.method == "GET" and request.url.path == "/envarena/openapi/v1/model-services/catalog/models/base-model":
            return httpx.Response(
                200,
                json={
                    "model_id": "base-model",
                    "model_name": "base-served",
                    "model_path": "/models/base",
                    "gateway": {},
                    "image": "base-image",
                    "resource_spec": {"cpu": 96, "gpu_num": 8},
                    "sglang": {"dtype": "bfloat16", "tp": 4},
                    "metadata": {"base": "yes"},
                    "exists": True,
                    "readable": True,
                    "weights_exist": True,
                    "weights_readable": True,
                    "availability_status": "AVAILABLE",
                    "created_at": "2026-08-03T00:00:00Z",
                    "updated_at": "2026-08-03T00:00:00Z",
                },
            )
        if request.method == "POST" and request.url.path == "/envarena/openapi/v1/model-services":
            assert payload == {
                "served_model_name": "override-served",
                "model_path": "/models/base",
                "image": "base-image",
                "replicas": 3,
                "resource_spec": {"cpu": 96, "gpu_num": 4},
                "sglang": {"dtype": "bfloat16", "tp": 2},
                "metadata": {"base": "yes", "request": "yes"},
            }
            return httpx.Response(
                201,
                json={
                    "service_id": "svc-base",
                    "served_model_name": "override-served",
                    "status": "PENDING",
                    "replicas": 3,
                    "desired_replicas": 3,
                    "readiness_policy": "any",
                    "gateway": {},
                    "ready_replicas": 0,
                    "instances": [],
                    "created_at": "2026-08-03T00:00:00Z",
                    "updated_at": "2026-08-03T00:00:00Z",
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    service = _client(handler).create_model_service(
        base_model_id="base-model",
        served_model_name="override-served",
        replicas=3,
        resource_spec={"gpu_num": 4},
        sglang={"tp": 2},
        metadata={"request": "yes"},
    )

    assert service["service_id"] == "svc-base"
    assert [method for method, _, _ in seen] == ["GET", "POST"]


def test_create_model_service_warns_when_base_model_catalog_config_cannot_be_loaded():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content or b"{}")
        if request.method == "GET" and request.url.path == "/envarena/openapi/v1/model-services/catalog/models/missing":
            return httpx.Response(404, json={"error": "not found"})
        if request.method == "POST" and request.url.path == "/envarena/openapi/v1/model-services":
            assert payload == {
                "served_model_name": "explicit",
                "model_path": "/models/explicit",
            }
            return httpx.Response(
                201,
                json={
                    "service_id": "svc-explicit",
                    "served_model_name": "explicit",
                    "status": "PENDING",
                    "replicas": 1,
                    "desired_replicas": 1,
                    "readiness_policy": "any",
                    "gateway": {},
                    "ready_replicas": 0,
                    "instances": [],
                    "created_at": "2026-08-03T00:00:00Z",
                    "updated_at": "2026-08-03T00:00:00Z",
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    with pytest.warns(RuntimeWarning, match="base model 'missing' could not be loaded"):
        service = _client(handler).create_model_service(
            base_model_id="missing",
            served_model_name="explicit",
            model_path="/models/explicit",
        )

    assert service["service_id"] == "svc-explicit"


def test_model_service_instance_stop_can_wait_for_parent_service_status_update():
    get_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal get_attempts
        if request.method == "POST" and request.url.path == "/envarena/openapi/v1/model-services/svc-1/instances/inst-1/stop":
            return httpx.Response(
                200,
                json={
                    "service_id": "svc-1",
                    "served_model_name": "model-a",
                    "status": "PENDING",
                    "replicas": 1,
                    "desired_replicas": 1,
                    "readiness_policy": "any",
                    "gateway": {},
                    "ready_replicas": 0,
                    "instances": [_model_service_instance("inst-1", status="PENDING")],
                    "created_at": "2026-08-03T00:00:00Z",
                    "updated_at": "2026-08-03T00:00:00Z",
                },
            )
        assert request.method == "GET"
        assert request.url.path == "/envarena/openapi/v1/model-services/svc-1"
        get_attempts += 1
        instance_status = "STOPPED" if get_attempts >= 2 else "PENDING"
        return httpx.Response(
            200,
            json={
                "service_id": "svc-1",
                "served_model_name": "model-a",
                "status": instance_status,
                "replicas": 1,
                "desired_replicas": 1,
                "readiness_policy": "any",
                "gateway": {},
                "ready_replicas": 0,
                "instances": [_model_service_instance("inst-1", status=instance_status)],
                "created_at": "2026-08-03T00:00:00Z",
                "updated_at": "2026-08-03T00:00:00Z",
            },
        )

    instance = _client(handler).stop_model_service_instance_and_wait(
        "svc-1",
        "inst-1",
        timeout_seconds=1,
        poll_seconds=0.001,
    )

    assert instance["instance_id"] == "inst-1"
    assert instance["status"] == "STOPPED"
    assert get_attempts == 2


def test_ensure_model_registry_for_ckpt_creates_service_waits_ready_and_publishes_model_ref():
    seen: list[tuple[str, str, dict]] = []
    get_attempts = 0

    def service_payload(status: str) -> dict[str, object]:
        return {
            "service_id": "svc-1",
            "served_model_name": "ckpt-alias",
            "status": status,
            "replicas": 1,
            "desired_replicas": 1,
            "readiness_policy": "any",
            "gateway": {},
            "ready_replicas": 1 if status == "READY" else 0,
            "instances": [_model_service_instance("inst-1", status="READY" if status == "READY" else "PENDING")],
            "created_at": "2026-08-03T00:00:00Z",
            "updated_at": "2026-08-03T00:00:00Z",
        }

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal get_attempts
        payload = json.loads(request.content or b"{}")
        seen.append((request.method, request.url.path, payload))
        path = request.url.path
        if request.method == "POST" and path == "/envarena/openapi/v1/model-services":
            assert payload == {
                "served_model_name": "ckpt-alias",
                "model_path": "/models/ckpt",
                "metadata": {"run_id": "run-1", "source": "areal-rsi"},
            }
            return httpx.Response(201, json=service_payload("PENDING"))
        if request.method == "GET" and path == "/envarena/openapi/v1/model-services/svc-1":
            get_attempts += 1
            return httpx.Response(200, json=service_payload("READY" if get_attempts >= 2 else "PENDING"))
        if request.method == "POST" and path == "/envarena/openapi/v1/models-register":
            assert payload == {
                "key": "model-key",
                "version": "2.0.0",
                "name": "Model Key",
                "description": "registered from ckpt",
                "tags": ["rsi"],
                "metadata": {
                    "run_id": "run-1",
                    "owner": "7",
                    "source": "areal-rsi",
                    "runtime_model_name": "ckpt-alias",
                    "model_service_id": "svc-1",
                    "model_path": "/models/ckpt",
                },
                "gateway_model_id": "ckpt-alias",
                "model_id": "ckpt-alias",
                "model_name": "ckpt-alias",
                "base_url": "https://arena.example.test/envarena/openapi/v1/llm",
                "api_key": "test-token",
                "profile": "dual",
                "parameters": {"temperature": 0.2},
                "env": {"A": "1"},
                "publish": True,
            }
            return httpx.Response(
                201,
                json=_model_version_payload(
                    "model-key",
                    "2.0.0",
                    parameters={"temperature": 0.2},
                    env={"A": "1"},
                ),
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    result = _client(handler).ensure_model_registry_for_ckpt(
        {"model_path": "/models/ckpt", "served_model_name": "ckpt-alias"},
        key="model-key",
        version="2.0.0",
        name="Model Key",
        description="registered from ckpt",
        tags=["rsi"],
        metadata={"run_id": "run-1"},
        ready_timeout_seconds=1,
        ready_poll_seconds=0.001,
        parameters={"temperature": 0.2},
        env={"A": "1"},
        job_config={"model_registry": {"metadata": {"owner": 7}}},
    )

    assert result["model_name"] == "ckpt-alias"
    assert result["registry_ref"] == {"key": "model-key", "version": "2.0.0"}
    assert result["asset"]["model_service"]["status"] == "READY"
    assert result["model_register"]["status"] == "PUBLISHED"
    assert get_attempts == 2
    assert [method for method, _, _ in seen] == ["POST", "GET", "GET", "POST"]


def test_ensure_model_registry_for_ckpt_forces_model_service_creation():
    seen: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content or b"{}")
        seen.append((request.method, request.url.path, payload))
        if request.method == "POST" and request.url.path == "/envarena/openapi/v1/model-services":
            assert payload == {
                "served_model_name": "ckpt-alias",
                "model_path": "/models/ckpt",
                "metadata": {"source": "areal-rsi"},
            }
            return httpx.Response(
                201,
                json={
                    "service_id": "svc-1",
                    "served_model_name": "ckpt-alias",
                    "status": "PENDING",
                    "replicas": 1,
                    "desired_replicas": 1,
                    "readiness_policy": "any",
                    "gateway": {},
                    "ready_replicas": 0,
                    "instances": [_model_service_instance("inst-1", status="PENDING")],
                    "created_at": "2026-08-03T00:00:00Z",
                    "updated_at": "2026-08-03T00:00:00Z",
                },
            )
        if request.method == "POST" and request.url.path == "/envarena/openapi/v1/models/model-key/versions":
            assert payload == {"version": "1.0.0", "spec": {"model_name": "ckpt-alias"}}
            return httpx.Response(201, json={"key": "model-key", "version": "1.0.0"})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    result = _client(handler).ensure_model_registry_for_ckpt(
        {"model_path": "/models/ckpt", "served_model_name": "ckpt-alias"},
        key="model-key",
        wait_ready=False,
        create_entity=False,
        publish=False,
        job_config={"model_service": {"enabled": False}},
    )

    assert result["registry_ref"] == {"key": "model-key", "version": "1.0.0"}
    assert result["asset"]["model_service"]["service_id"] == "svc-1"
    assert [method for method, _, _ in seen] == ["POST", "POST"]


def test_submit_eval_by_existed_stream_launches_batch_task_with_model_ref():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = json.loads(request.content or b"{}")
        if request.method == "GET" and path == "/envarena/openapi/v1/streams/train-stream":
            return httpx.Response(
                200,
                json={
                    "stream": {
                        "stream_id": "train-stream",
                        "name": "Train Stream",
                        "task": "swe",
                        "selector": {"include_tags": ["train"]},
                        "default_harness_ref": {"key": "base-harness", "version": "1.0.0"},
                        "default_reward_ref": {"key": "base-reward", "version": "1.0.0"},
                        "status": "ACTIVE",
                    }
                },
            )
        if request.method == "POST" and path == "/envarena/openapi/v1/streams/train-stream/dataset":
            assert request.url.params["limit"] == "1000"
            return httpx.Response(
                200,
                json={
                    "data_ids": ["env:env-a@1.0.0"],
                    "items": [{"data_id": "env:env-a@1.0.0", "env_ref": {"key": "env-a", "version": "1.0.0"}}],
                    "count": 1,
                    "total": 1,
                    "offset": 0,
                    "limit": 1000,
                },
            )
        if request.method == "POST" and path == "/envarena/openapi/v1/streams/train-stream/launch_batch_task":
            assert payload["model_ref"] == {"key": "model-a", "version": "1.0.0"}
            assert payload["harness_ref"] == {"key": "base-harness", "version": "1.0.0"}
            assert payload["reward_ref"] == {"key": "base-reward", "version": "1.0.0"}
            assert payload["tasks"] == [{"data_id": "env:env-a@1.0.0"}]
            assert payload["metadata"]["source_stream_id"] == "train-stream"
            return httpx.Response(
                202,
                json={
                    "accepted": True,
                    "stream_id": "train-stream",
                    "job_id": "job-stream",
                    "task_ids": ["task-1"],
                    "job": _benchmark_job_payload("job-stream"),
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    job = _client(handler).submit_eval_by_existed_stream(
        {"type": "ref", "ref": "model-a@1.0.0"},
        "train-stream",
    )

    assert job["eval_job_id"] == "job-stream"
    assert job["stream_id"] == "train-stream"
    assert job["selected_data_ids"] == ["env:env-a@1.0.0"]
    assert job["model_ref"]["registry_ref"] == {"key": "model-a", "version": "1.0.0"}
    assert job["assets"]["stream_tasks"] == [{"data_id": "env:env-a@1.0.0"}]


def test_submit_eval_by_fork_stream_creates_skill_harness_fork_stream_and_launches_batch_task():
    keys: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = json.loads(request.content or b"{}")
        if request.method == "GET" and path == "/envarena/openapi/v1/streams/source-stream":
            return httpx.Response(
                200,
                json={
                    "stream": {
                        "stream_id": "source-stream",
                        "name": "Source",
                        "task": "swe",
                        "selector": {"include_tags": ["train"]},
                        "default_harness_ref": {"key": "base-harness", "version": "1.0.0"},
                        "default_reward_ref": {"key": "base-reward", "version": "1.0.0"},
                        "status": "ACTIVE",
                    }
                },
            )
        if request.method == "GET" and keys.get("fork_stream") and path == f"/envarena/openapi/v1/streams/{keys['fork_stream']}":
            return httpx.Response(
                200,
                json={
                    "stream": {
                        "stream_id": keys["fork_stream"],
                        "name": "Fork",
                        "task": "swe",
                        "selector": {"include_tags": ["train"]},
                        "default_harness_ref": {"key": keys["harness"], "version": "1.0.0"},
                        "default_reward_ref": {"key": "base-reward", "version": "1.0.0"},
                        "metadata": keys["fork_metadata"],
                        "status": "ACTIVE",
                    }
                },
            )
        if request.method == "POST" and path == "/envarena/openapi/v1/streams/source-stream/dataset":
            return httpx.Response(
                200,
                json={
                    "data_ids": ["env:env-a@1.0.0", "env:env-b@1.0.0"],
                    "items": [
                        {"data_id": "env:env-a@1.0.0", "env_ref": {"key": "env-a", "version": "1.0.0"}},
                        {"data_id": "env:env-b@1.0.0", "env_ref": {"key": "env-b", "version": "1.0.0"}},
                    ],
                    "count": 2,
                    "total": 2,
                    "offset": 0,
                    "limit": 1000,
                },
            )
        if request.method == "POST" and path == "/envarena/openapi/v1/skills":
            assert payload["key"] == "skill-new"
            return httpx.Response(201, json={})
        if request.method == "POST" and path == "/envarena/openapi/v1/skills/skill-new/versions":
            assert payload["spec"] == {"bundle": {"uri": "oss://skill"}, "manifest": {"name": "skill-new", "description": "d"}}
            return httpx.Response(201, json={})
        if request.method == "POST" and path == "/envarena/openapi/v1/skills/skill-new/versions/1.0.0/publish":
            return httpx.Response(200, json={})
        if request.method == "GET" and path == "/envarena/openapi/v1/harnesses/base-harness/versions/1.0.0":
            return httpx.Response(
                200,
                json={
                    "key": "base-harness",
                    "version": "1.0.0",
                    "spec": {
                        "runtime": {"type": "claude_code", "uri": "oss://runtime"},
                        "ttl_seconds": 600,
                    },
                },
            )
        if request.method == "POST" and path == "/envarena/openapi/v1/harnesses":
            keys["harness"] = payload["key"]
            assert keys["harness"].startswith("areal-rsi-base-harness-fork-harness-")
            return httpx.Response(201, json={})
        if request.method == "POST" and path == f"/envarena/openapi/v1/harnesses/{keys['harness']}/versions":
            assert payload["spec"]["skill_refs"] == [
                {"key": "skill-new", "version": "1.0.0"},
                {"key": "skill-existing", "version": "2.0.0"},
            ]
            return httpx.Response(201, json={})
        if request.method == "POST" and path == f"/envarena/openapi/v1/harnesses/{keys['harness']}/versions/1.0.0/publish":
            return httpx.Response(200, json={})
        if request.method == "POST" and path == "/envarena/openapi/v1/streams":
            keys["fork_stream"] = payload["stream_id"]
            keys["fork_metadata"] = payload["metadata"]
            assert payload["default_harness_ref"] == {"key": keys["harness"], "version": "1.0.0"}
            assert "benchmark_ref" not in payload["metadata"]
            assert json.loads(payload["metadata"]["relationships"]) == [
                {"data_id": "env:env-a@1.0.0", "experience_id": "exp-1"}
            ]
            fork_items = json.loads(payload["metadata"]["fork_items"])
            assert fork_items == [
                {
                    "source_stream_id": "source-stream",
                    "source_data_id": "env:env-a@1.0.0",
                    "data_id": "env:env-a@1.0.0",
                    "env_ref": {"key": "env-a", "version": "1.0.0"},
                    "harness_ref": {"key": keys["harness"], "version": "1.0.0"},
                }
            ]
            return httpx.Response(201, json={"stream_id": keys["fork_stream"], "status": "ACTIVE"})
        if request.method == "POST" and path == f"/envarena/openapi/v1/streams/{keys['fork_stream']}/launch_batch_task":
            assert payload["model_ref"] == {"key": "model-a", "version": "1.0.0"}
            assert payload["harness_ref"] == {"key": keys["harness"], "version": "1.0.0"}
            assert payload["reward_ref"] == {"key": "base-reward", "version": "1.0.0"}
            assert payload["tasks"] == [{"data_id": "env:env-a@1.0.0"}]
            assert payload["metadata"]["fork_stream_id"] == keys["fork_stream"]
            return httpx.Response(
                202,
                json={
                    "accepted": True,
                    "stream_id": keys["fork_stream"],
                    "job_id": "job-fork",
                    "task_ids": ["task-1"],
                    "job": _benchmark_job_payload("job-fork", harness_key=keys["harness"]),
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    job = _client(handler).submit_eval_by_fork_stream(
        {"type": "ref", "ref": "model-a@1.0.0"},
        "source-stream",
        ["env:env-a@1.0.0"],
        [
            {
                "type": "new",
                "key": "skill-new",
                "version": "1.0.0",
                "spec": {"bundle": {"uri": "oss://skill"}, "manifest": {"name": "skill-new", "description": "d"}},
            },
            "skill-existing@2.0.0",
        ],
        [{"data_id": "env:env-a@1.0.0", "experience_id": "exp-1"}],
    )

    assert job["eval_job_id"] == "job-fork"
    assert job["fork_stream_id"] == keys["fork_stream"]
    assert job["selected_data_ids"] == ["env:env-a@1.0.0"]


def test_submit_eval_by_fork_stream_uses_experience_bound_skill_as_task_harness_override():
    keys: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = json.loads(request.content or b"{}")
        if request.method == "GET" and path == "/envarena/openapi/v1/streams/source-stream":
            return httpx.Response(
                200,
                json={
                    "stream": {
                        "stream_id": "source-stream",
                        "name": "Source",
                        "task": "swe",
                        "selector": {"include_tags": ["train"]},
                        "default_harness_ref": {"key": "base-harness", "version": "1.0.0"},
                        "default_reward_ref": {"key": "base-reward", "version": "1.0.0"},
                        "status": "ACTIVE",
                    }
                },
            )
        if request.method == "GET" and keys.get("fork_stream") and path == f"/envarena/openapi/v1/streams/{keys['fork_stream']}":
            return httpx.Response(
                200,
                json={
                    "stream": {
                        "stream_id": keys["fork_stream"],
                        "name": "Fork",
                        "task": "swe",
                        "selector": {"include_tags": ["train"]},
                        "default_harness_ref": {"key": "base-harness", "version": "1.0.0"},
                        "default_reward_ref": {"key": "base-reward", "version": "1.0.0"},
                        "metadata": keys["fork_metadata"],
                        "status": "ACTIVE",
                    }
                },
            )
        if request.method == "POST" and path == "/envarena/openapi/v1/streams/source-stream/dataset":
            return httpx.Response(
                200,
                json={
                    "data_ids": ["env:env-a@1.0.0"],
                    "items": [{"data_id": "env:env-a@1.0.0", "env_ref": {"key": "env-a", "version": "1.0.0"}}],
                    "count": 1,
                    "total": 1,
                    "offset": 0,
                    "limit": 1000,
                },
            )
        if request.method == "GET" and path == "/envarena/openapi/v1/harnesses/base-harness/versions/1.0.0":
            return httpx.Response(
                200,
                json={
                    "key": "base-harness",
                    "version": "1.0.0",
                    "spec": {"runtime": {"type": "claude_code", "uri": "oss://runtime"}},
                },
            )
        if request.method == "POST" and path == "/envarena/openapi/v1/harnesses":
            keys["task_harness"] = payload["key"]
            assert keys["task_harness"].startswith("areal-rsi-base-harness-env-env-a-1.0.0-fork-harness-")
            return httpx.Response(201, json={})
        if request.method == "POST" and path == f"/envarena/openapi/v1/harnesses/{keys['task_harness']}/versions":
            assert payload["spec"]["skill_refs"] == [{"key": "skill-a", "version": "1.0.0"}]
            assert payload["spec"]["metadata"]["data_id"] == "env:env-a@1.0.0"
            return httpx.Response(201, json={})
        if request.method == "POST" and path == f"/envarena/openapi/v1/harnesses/{keys['task_harness']}/versions/1.0.0/publish":
            return httpx.Response(200, json={})
        if request.method == "POST" and path == "/envarena/openapi/v1/streams":
            keys["fork_stream"] = payload["stream_id"]
            keys["fork_metadata"] = payload["metadata"]
            assert payload["default_harness_ref"] == {"key": "base-harness", "version": "1.0.0"}
            fork_items = json.loads(payload["metadata"]["fork_items"])
            assert fork_items == [
                {
                    "source_stream_id": "source-stream",
                    "source_data_id": "env:env-a@1.0.0",
                    "data_id": "env:env-a@1.0.0",
                    "env_ref": {"key": "env-a", "version": "1.0.0"},
                    "harness_ref": {"key": keys["task_harness"], "version": "1.0.0"},
                }
            ]
            return httpx.Response(201, json={"stream_id": keys["fork_stream"], "status": "ACTIVE"})
        if request.method == "POST" and path == f"/envarena/openapi/v1/streams/{keys['fork_stream']}/launch_batch_task":
            assert payload["harness_ref"] == {"key": "base-harness", "version": "1.0.0"}
            assert payload["tasks"] == [
                {
                    "data_id": "env:env-a@1.0.0",
                    "harness_ref": {"key": keys["task_harness"], "version": "1.0.0"},
                }
            ]
            return httpx.Response(
                202,
                json={
                    "accepted": True,
                    "stream_id": keys["fork_stream"],
                    "job_id": "job-fork",
                    "task_ids": ["task-1"],
                    "job": _benchmark_job_payload("job-fork", harness_key="base-harness"),
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    job = _client(handler).submit_eval_by_fork_stream(
        {"type": "ref", "ref": "model-a@1.0.0"},
        "source-stream",
        ["env:env-a@1.0.0"],
        [{"type": "ref", "ref": "skill-a@1.0.0", "experience_id": "exp-1"}],
        [{"data_id": "env:env-a@1.0.0", "experience_id": "exp-1"}],
    )

    assert job["assets"]["stream_tasks"] == [
        {
            "data_id": "env:env-a@1.0.0",
            "harness_ref": {"key": keys["task_harness"], "version": "1.0.0"},
        }
    ]
