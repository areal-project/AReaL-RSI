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


def _model_service_catalog_payload(
    model_id: str = "base-model",
    *,
    model_name: str = "base-served",
    model_path: str = "/models/base",
) -> dict[str, object]:
    return {
        "model_id": model_id,
        "model_name": model_name,
        "model_path": model_path,
        "gateway": {},
        "exists": True,
        "readable": True,
        "weights_exist": True,
        "weights_readable": True,
        "availability_status": "AVAILABLE",
        "created_at": "2026-08-03T00:00:00Z",
        "updated_at": "2026-08-03T00:00:00Z",
    }


def _stream_validation_handler(data_ids: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/envarena/openapi/v1/streams/train-stream":
            return httpx.Response(
                200,
                json={
                    "stream": {
                        "stream_id": "train-stream",
                        "name": "Train Stream",
                        "task": "swe",
                        "default_harness_ref": {"key": "base-harness", "version": "1.0.0"},
                        "default_reward_ref": {"key": "base-reward", "version": "1.0.0"},
                        "status": "ACTIVE",
                    }
                },
            )
        if request.method == "POST" and path == "/envarena/openapi/v1/streams/train-stream/dataset":
            return httpx.Response(
                200,
                json={
                    "data_ids": data_ids,
                    "items": [
                        {"data_id": data_id, "env_ref": {"key": f"env-{index}", "version": "1.0.0"}}
                        for index, data_id in enumerate(data_ids)
                    ],
                    "count": len(data_ids),
                    "total": len(data_ids),
                    "offset": 0,
                    "limit": 1000,
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    return handler


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
    assert client.get_eval_progress("job_001")["job"]["status"] == "RUNNING"
    assert client.get_eval_result(job)["aggregate_score"] == 0.8
    assert client.get_eval_result("job_001")["aggregate_score"] == 0.8
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
    assert len(seen) == 9


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
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "task_id": "task-3",
                            "status": "DONE",
                            "score": 1.0,
                            "env_ref": {"key": "env-3", "version": "1.2.3"},
                        }
                    ],
                    "total": 1,
                },
            )
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
    assert trajs[0]["data_id"] == "env:env-3@1.2.3"
    assert trajs[0]["metadata"]["data_id"] == "env:env-3@1.2.3"
    assert "messages" not in trajs[0]

    job_id_trajs = client.get_trajs("job_003")
    assert job_id_trajs[0]["metadata"]["source"] == "eval"
    assert job_id_trajs[0]["reward"] == 1.0


def test_get_trajs_uses_eval_job_data_id_mapping_when_task_row_lacks_data_id():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/envarena/openapi/v1/jobs/job_004/tasks":
            return httpx.Response(200, json={"items": [{"task_id": "task-a", "status": "DONE", "score": 0.5}], "total": 1})
        if request.url.path == "/envarena/openapi/v1/jobs/job_004/trajectories":
            return httpx.Response(200, json={"tasks": [{"task_id": "task-a", "latest_attempt": 1}]})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    trajs = _client(handler).get_trajs(
        {
            "eval_job_id": "job_004",
            "model_ref": {"model_name": "stable-alias"},
            "harness_ref": {"key": "harness"},
            "task_ids": ["task-a"],
            "selected_data_ids": ["env:selected-task@1.0.0"],
            "assets": {"stream_tasks": [{"data_id": "env:selected-task@1.0.0"}]},
        }
    )

    assert trajs[0]["data_id"] == "env:selected-task@1.0.0"
    assert trajs[0]["metadata"]["data_id"] == "env:selected-task@1.0.0"


def test_get_trajs_can_lookup_data_id_from_stream_task_using_job_metadata_stream_id():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/envarena/openapi/v1/jobs/job_005/tasks":
            return httpx.Response(200, json={"items": [{"task_id": "task-a", "status": "DONE"}], "total": 1})
        if request.url.path == "/envarena/openapi/v1/jobs/job_005/trajectories":
            return httpx.Response(200, json={"tasks": [{"task_id": "task-a", "latest_attempt": 1}]})
        if request.url.path == "/envarena/openapi/v1/jobs/job_005/summary":
            return httpx.Response(
                200,
                json={"job": {"job_id": "job_005", "metadata": {"stream_id": "swe-bench-verified"}}},
            )
        if request.url.path == "/envarena/openapi/v1/streams/swe-bench-verified/tasks/task-a":
            return httpx.Response(200, json={"data_id": "env:from-stream-task@2.0.0"})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    trajs = _client(handler).get_trajs(
        {
            "eval_job_id": "job_005",
            "model_ref": {"model_name": "stable-alias"},
            "harness_ref": {"key": "harness"},
        }
    )

    assert trajs[0]["data_id"] == "env:from-stream-task@2.0.0"
    assert trajs[0]["metadata"]["data_id"] == "env:from-stream-task@2.0.0"


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
        if request.method == "GET" and request.url.path == "/envarena/openapi/v1/model-services/catalog/models/base-model":
            return httpx.Response(200, json=_model_service_catalog_payload())
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
        {
            "type": "ckpt",
            "model_path": "/models/ckpt",
            "served_model_name": "ckpt-alias",
            "base_model_id": "base-model",
        },
        metadata={"run_id": "run-1"},
    )

    assert model["model_name"] == "ckpt-alias"
    assert model["asset"]["model_service"]["service_id"] == "svc-1"


@pytest.mark.parametrize(
    "model_config",
    [
        "/models/ckpt",
        {"model_path": "/models/ckpt", "served_model_name": "ckpt-alias", "base_model_id": "base-model"},
        {"type": "ckpt", "model_path": "/models/ckpt", "served_model_name": "ckpt-alias"},
        {"type": "ref", "ref": ""},
        {"type": "new", "model_path": "/models/ckpt"},
    ],
)
def test_ensure_model_service_for_ckpt_rejects_non_strict_model_config(model_config):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    with pytest.raises(EnvArenaConfigurationError):
        _client(handler).ensure_model_service_for_ckpt(model_config)


def test_ensure_model_service_for_ckpt_uses_model_ref_without_http_requests():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    model = _client(handler).ensure_model_service_for_ckpt(
        {"type": "ref", "ref": "model-a@1.0.0"}
    )

    assert model["model_name"] == "model-a"
    assert model["registry_ref"].to_dict() == {"key": "model-a", "version": "1.0.0"}
    assert model["asset"] == {
        "model_name": "model-a",
        "registry_ref": {"key": "model-a", "version": "1.0.0"},
    }


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
        if request.method == "GET" and path == "/envarena/openapi/v1/model-services/catalog/models/base-model":
            return httpx.Response(200, json=_model_service_catalog_payload())
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
        {
            "type": "ckpt",
            "model_path": "/models/ckpt",
            "served_model_name": "ckpt-alias",
            "base_model_id": "base-model",
        },
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
    assert [method for method, _, _ in seen] == ["GET", "POST", "GET", "GET", "POST"]


def test_ensure_model_registry_for_ckpt_forces_model_service_creation():
    seen: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content or b"{}")
        seen.append((request.method, request.url.path, payload))
        if request.method == "GET" and request.url.path == "/envarena/openapi/v1/model-services/catalog/models/base-model":
            return httpx.Response(200, json=_model_service_catalog_payload())
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
        {
            "type": "ckpt",
            "model_path": "/models/ckpt",
            "served_model_name": "ckpt-alias",
            "base_model_id": "base-model",
        },
        key="model-key",
        wait_ready=False,
        create_entity=False,
        publish=False,
        job_config={"model_service": {"enabled": False}},
    )

    assert result["registry_ref"] == {"key": "model-key", "version": "1.0.0"}
    assert result["asset"]["model_service"]["service_id"] == "svc-1"
    assert [method for method, _, _ in seen] == ["GET", "POST", "POST"]


def test_submit_eval_by_stream_launches_batch_task_with_model_ref():
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
            assert payload["metadata"]["stream_id"] == "train-stream"
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

    job = _client(handler).submit_eval_by_stream(
        model={"type": "ref", "ref": "model-a@1.0.0"},
        stream_id="train-stream",
    )

    assert job["eval_job_id"] == "job-stream"
    assert job["stream_id"] == "train-stream"
    assert job["selected_data_ids"] == ["env:env-a@1.0.0"]
    assert job["model_ref"]["registry_ref"] == {"key": "model-a", "version": "1.0.0"}
    assert job["assets"]["stream_tasks"] == [{"data_id": "env:env-a@1.0.0"}]


def test_submit_eval_by_stream_defaults_to_all_data_ids_and_applies_per_data_rewards():
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
                        "default_harness_ref": {"key": "base-harness", "version": "1.0.0"},
                        "default_reward_ref": {"key": "base-reward", "version": "1.0.0"},
                        "status": "ACTIVE",
                    }
                },
            )
        if request.method == "POST" and path == "/envarena/openapi/v1/streams/train-stream/dataset":
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
        if request.method == "POST" and path == "/envarena/openapi/v1/streams/train-stream/launch_batch_task":
            assert payload["reward_ref"] == {"key": "base-reward", "version": "1.0.0"}
            assert payload["tasks"] == [
                {
                    "data_id": "env:env-a@1.0.0",
                    "reward_ref": {"key": "reward-a", "version": "1.0.0"},
                },
                {
                    "data_id": "env:env-b@1.0.0",
                    "reward_ref": {"key": "reward-b", "version": "2.0.0"},
                },
            ]
            return httpx.Response(
                202,
                json={
                    "accepted": True,
                    "stream_id": "train-stream",
                    "job_id": "job-stream",
                    "task_ids": ["task-1", "task-2"],
                    "job": _benchmark_job_payload("job-stream"),
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    job = _client(handler).submit_eval_by_stream(
        {"type": "ref", "ref": "model-a@1.0.0"},
        "train-stream",
        rewards=[
            {"data_id": "env:env-a@1.0.0", "ref": "reward-a@1.0.0"},
            {"data_id": "env:env-b@1.0.0", "ref": {"type": "ref", "ref": "reward-b@2.0.0"}},
        ],
    )

    assert job["selected_data_ids"] == ["env:env-a@1.0.0", "env:env-b@1.0.0"]
    assert job["assets"]["stream_tasks"][1]["reward_ref"] == {"key": "reward-b", "version": "2.0.0"}


def test_submit_eval_by_stream_allows_partial_skill_bindings_for_selected_data_ids():
    keys: dict[str, str] = {}

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
                        "default_harness_ref": {"key": "base-harness", "version": "1.0.0"},
                        "default_reward_ref": {"key": "base-reward", "version": "1.0.0"},
                        "status": "ACTIVE",
                    }
                },
            )
        if request.method == "POST" and path == "/envarena/openapi/v1/streams/train-stream/dataset":
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
            keys["harness"] = payload["key"]
            assert keys["harness"].startswith("areal-rsi-base-harness-env-env-a-1.0.0-stream-harness-")
            return httpx.Response(201, json={})
        if request.method == "POST" and path == f"/envarena/openapi/v1/harnesses/{keys['harness']}/versions":
            assert payload["spec"]["skill_refs"] == [{"key": "skill-a", "version": "1.0.0"}]
            return httpx.Response(201, json={})
        if request.method == "POST" and path == f"/envarena/openapi/v1/harnesses/{keys['harness']}/versions/1.0.0/publish":
            return httpx.Response(200, json={})
        if request.method == "POST" and path == "/envarena/openapi/v1/streams/train-stream/launch_batch_task":
            assert payload["tasks"] == [
                {
                    "data_id": "env:env-a@1.0.0",
                    "harness_ref": {"key": keys["harness"], "version": "1.0.0"},
                },
                {"data_id": "env:env-b@1.0.0"},
            ]
            return httpx.Response(
                202,
                json={
                    "accepted": True,
                    "stream_id": "train-stream",
                    "job_id": "job-stream",
                    "task_ids": ["task-1", "task-2"],
                    "job": _benchmark_job_payload("job-stream", harness_key="base-harness"),
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    job = _client(handler).submit_eval_by_stream(
        {"type": "ref", "ref": "model-a@1.0.0"},
        "train-stream",
        skills=[{"data_id": "env:env-a@1.0.0", "type": "ref", "ref": "skill-a@1.0.0"}],
    )

    assert job["selected_data_ids"] == ["env:env-a@1.0.0", "env:env-b@1.0.0"]
    assert job["assets"]["stream_tasks"] == [
        {
            "data_id": "env:env-a@1.0.0",
            "harness_ref": {"key": keys["harness"], "version": "1.0.0"},
        },
        {"data_id": "env:env-b@1.0.0"},
    ]


def test_submit_eval_by_stream_rejects_multiple_skill_bindings_for_same_data_id():
    with pytest.raises(EnvArenaConfigurationError, match="multiple skill bindings"):
        _client(_stream_validation_handler(["env:env-a@1.0.0"])).submit_eval_by_stream(
            {"type": "ref", "ref": "model-a@1.0.0"},
            "train-stream",
            skills=[
                {
                    "data_id": "env:env-a@1.0.0",
                    "type": "experience",
                    "content": "new hint",
                },
                {
                    "data_id": "env:env-a@1.0.0",
                    "type": "ref",
                    "ref": "skill-a@1.0.0",
                },
            ],
        )


def test_submit_eval_by_stream_rejects_ref_skill_without_ref_field():
    with pytest.raises(EnvArenaConfigurationError, match="ref skill bindings require ref"):
        _client(_stream_validation_handler(["env:env-a@1.0.0"])).submit_eval_by_stream(
            {"type": "ref", "ref": "model-a@1.0.0"},
            "train-stream",
            skills=[
                {
                    "data_id": "env:env-a@1.0.0",
                    "type": "ref",
                    "content": "skill-a@1.0.0",
                }
            ],
        )


@pytest.mark.parametrize(
    ("rewards", "error_match"),
    [
        (
            [
                {"data_id": "env:env-a@1.0.0", "ref": "reward-a@1.0.0"},
                {"data_id": "env:env-a@1.0.0", "ref": "reward-b@1.0.0"},
            ],
            "multiple reward bindings",
        ),
        (
            [{"data_id": "env:env-b@1.0.0", "ref": "reward-b@1.0.0"}],
            "is not selected",
        ),
        (
            [{"data_id": "env:env-a@1.0.0"}],
            "requires ref",
        ),
    ],
)
def test_submit_eval_by_stream_rejects_invalid_reward_bindings(
    rewards: list[dict[str, str]],
    error_match: str,
):
    with pytest.raises(EnvArenaConfigurationError, match=error_match):
        _client(_stream_validation_handler(["env:env-a@1.0.0"])).submit_eval_by_stream(
            {"type": "ref", "ref": "model-a@1.0.0"},
            "train-stream",
            rewards=rewards,
        )


def test_submit_eval_by_stream_creates_skill_harness_and_launches_batch_task():
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
            assert keys["harness"].startswith("areal-rsi-base-harness-env-env-a-1.0.0-stream-harness-")
            return httpx.Response(201, json={})
        if request.method == "POST" and path == f"/envarena/openapi/v1/harnesses/{keys['harness']}/versions":
            assert payload["spec"]["skill_refs"] == [{"key": "skill-a", "version": "1.0.0"}]
            return httpx.Response(201, json={})
        if request.method == "POST" and path == f"/envarena/openapi/v1/harnesses/{keys['harness']}/versions/1.0.0/publish":
            return httpx.Response(200, json={})
        if request.method == "POST" and path == "/envarena/openapi/v1/streams/source-stream/launch_batch_task":
            assert payload["model_ref"] == {"key": "model-a", "version": "1.0.0"}
            assert payload["harness_ref"] == {"key": "base-harness", "version": "1.0.0"}
            assert payload["reward_ref"] == {"key": "base-reward", "version": "1.0.0"}
            assert payload["tasks"] == [
                {
                    "data_id": "env:env-a@1.0.0",
                    "harness_ref": {"key": keys["harness"], "version": "1.0.0"},
                }
            ]
            assert payload["metadata"]["stream_id"] == "source-stream"
            assert payload["metadata"]["source_stream_id"] == "source-stream"
            assert payload["metadata"]["stream_eval_mode"] == "stream"
            assert "fork_stream_id" not in payload["metadata"]
            return httpx.Response(
                202,
                json={
                    "accepted": True,
                    "stream_id": "source-stream",
                    "job_id": "job-stream",
                    "task_ids": ["task-1"],
                    "job": _benchmark_job_payload("job-stream", harness_key="base-harness"),
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    job = _client(handler).submit_eval_by_stream(
        {"type": "ref", "ref": "model-a@1.0.0"},
        "source-stream",
        data_ids=["env:env-a@1.0.0"],
        skills=[
            {
                "data_id": "env:env-a@1.0.0",
                "type": "ref",
                "ref": "skill-a@1.0.0",
            },
        ],
    )

    assert job["eval_job_id"] == "job-stream"
    assert "fork_stream_id" not in job
    assert "fork_stream" not in job["assets"]
    assert job["stream_id"] == "source-stream"
    assert job["selected_data_ids"] == ["env:env-a@1.0.0"]
    assert job["assets"]["stream_tasks"] == [
        {
            "data_id": "env:env-a@1.0.0",
            "harness_ref": {"key": keys["harness"], "version": "1.0.0"},
        }
    ]


def test_submit_eval_by_stream_uses_bound_skill_as_task_harness_override():
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
            assert keys["task_harness"].startswith("areal-rsi-base-harness-env-env-a-1.0.0-stream-harness-")
            return httpx.Response(201, json={})
        if request.method == "POST" and path == f"/envarena/openapi/v1/harnesses/{keys['task_harness']}/versions":
            assert payload["spec"]["skill_refs"] == [{"key": "skill-a", "version": "1.0.0"}]
            assert payload["spec"]["metadata"]["data_id"] == "env:env-a@1.0.0"
            return httpx.Response(201, json={})
        if request.method == "POST" and path == f"/envarena/openapi/v1/harnesses/{keys['task_harness']}/versions/1.0.0/publish":
            return httpx.Response(200, json={})
        if request.method == "POST" and path == "/envarena/openapi/v1/streams/source-stream/launch_batch_task":
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
                    "stream_id": "source-stream",
                    "job_id": "job-stream",
                    "task_ids": ["task-1"],
                    "job": _benchmark_job_payload("job-stream", harness_key="base-harness"),
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    job = _client(handler).submit_eval_by_stream(
        {"type": "ref", "ref": "model-a@1.0.0"},
        "source-stream",
        data_ids=["env:env-a@1.0.0"],
        skills=[{"data_id": "env:env-a@1.0.0", "type": "ref", "ref": "skill-a@1.0.0"}],
    )

    assert job["assets"]["stream_tasks"] == [
        {
            "data_id": "env:env-a@1.0.0",
            "harness_ref": {"key": keys["task_harness"], "version": "1.0.0"},
        }
    ]


def test_submit_eval_by_stream_updates_stream_data_skill_and_harness_versions_from_experience():
    keys: dict[str, str | dict] = {}
    data_id = "env:env-a@1.0.0"

    def source_stream() -> dict[str, object]:
        return {
            "stream": {
                "stream_id": "source-stream",
                "name": "Source",
                "task": "swe",
                "selector": {"include_tags": ["train"]},
                "default_harness_ref": {"key": "base-harness", "version": "1.0.0"},
                "default_reward_ref": {"key": "base-reward", "version": "1.0.0"},
                "status": "ACTIVE",
            }
        }

    def dataset() -> dict[str, object]:
        return {
            "data_ids": [data_id],
            "items": [
                {
                    "data_id": data_id,
                    "env_ref": {"key": "env-a", "version": "1.0.0"},
                    "harness_ref": {"key": "env-a-harness", "version": "1.0.0"},
                }
            ],
            "count": 1,
            "total": 1,
            "offset": 0,
            "limit": 1000,
        }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = json.loads(request.content or b"{}")
        if request.method == "GET" and path == "/envarena/openapi/v1/streams/source-stream":
            return httpx.Response(200, json=source_stream())
        if request.method == "POST" and path == "/envarena/openapi/v1/streams/source-stream/dataset":
            return httpx.Response(200, json=dataset())
        if request.method == "GET" and path == "/envarena/openapi/v1/harnesses/env-a-harness/versions/1.0.0":
            return httpx.Response(
                200,
                json={
                    "key": "env-a-harness",
                    "version": "1.0.0",
                    "spec": {
                        "runtime": {"type": "claude_code", "uri": "oss://runtime"},
                        "ttl_seconds": 600,
                        "skill_refs": [{"key": "skill-a", "version": "1.0.0"}],
                    },
                },
            )
        if request.method == "GET" and path == "/envarena/openapi/v1/skills/skill-a/versions/1.0.0":
            return httpx.Response(
                200,
                json={
                    "key": "skill-a",
                    "version": "1.0.0",
                    "spec": {
                        "bundle": {"uri": "oss://skills/skill-a"},
                        "manifest": {"name": "skill-a", "description": "base skill"},
                    },
                },
            )
        if request.method == "POST" and path == "/envarena/openapi/v1/skills/skill-a/versions":
            assert payload["version"] == "1.0.1"
            assert payload["supersedes"] == "1.0.0"
            assert payload["spec"]["bundle"] == {"uri": "oss://skills/skill-a"}
            assert payload["spec"]["task_experiences"] == [
                {
                    "schema_version": "task-experience.v1",
                    "task_id": data_id,
                    "status": "completed",
                    "experience": "new hint",
                }
            ]
            return httpx.Response(201, json={"key": "skill-a", "version": "1.0.1", "spec": payload["spec"]})
        if request.method == "POST" and path == "/envarena/openapi/v1/skills/skill-a/versions/1.0.1/publish":
            return httpx.Response(200, json={"key": "skill-a", "version": "1.0.1", "status": "PUBLISHED"})
        if request.method == "POST" and path == "/envarena/openapi/v1/harnesses":
            raise AssertionError("experience updates must not create a new harness entity")
        if request.method == "POST" and path == "/envarena/openapi/v1/harnesses/env-a-harness/versions":
            assert payload["version"] == "1.0.1"
            assert payload["supersedes"] == "1.0.0"
            assert payload["spec"]["skill_refs"] == [{"key": "skill-a", "version": "1.0.1"}]
            assert payload["spec"]["metadata"]["data_id"] == data_id
            assert payload["spec"]["metadata"]["seed"] == "20260806"
            return httpx.Response(201, json={"key": "env-a-harness", "version": "1.0.1", "spec": payload["spec"]})
        if request.method == "POST" and path == "/envarena/openapi/v1/harnesses/env-a-harness/versions/1.0.1/publish":
            return httpx.Response(200, json={"key": "env-a-harness", "version": "1.0.1", "status": "PUBLISHED"})
        if request.method == "POST" and path == "/envarena/openapi/v1/streams/source-stream/launch_batch_task":
            assert payload["model_ref"] == {"key": "model-a", "version": "1.0.0"}
            assert payload["harness_ref"] == {"key": "base-harness", "version": "1.0.0"}
            assert payload["tasks"] == [
                {
                    "data_id": data_id,
                    "harness_ref": {"key": "env-a-harness", "version": "1.0.1"},
                }
            ]
            return httpx.Response(
                202,
                json={
                    "accepted": True,
                    "stream_id": "source-stream",
                    "job_id": "job-stream",
                    "task_ids": ["task-1"],
                    "job": _benchmark_job_payload("job-stream", harness_key="base-harness"),
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    job = _client(handler).submit_eval_by_stream(
        {"type": "ref", "ref": "model-a@1.0.0"},
        "source-stream",
        data_ids=[data_id],
        skills=[{"data_id": data_id, "type": "experience", "content": "new hint"}],
        job_config={
            "experience_skill": {"version": "1.0.1"},
            "experience_harness": {"version": "1.0.1"},
        },
        metadata={"seed": 20260806},
    )

    assert job["eval_job_id"] == "job-stream"
    assert job["assets"]["experience_updates"][0]["skill_ref"] == {"key": "skill-a", "version": "1.0.1"}
    assert job["assets"]["skills"] == [{"key": "skill-a", "version": "1.0.1"}]
    assert job["assets"]["stream_tasks"] == [
        {
            "data_id": data_id,
            "harness_ref": {"key": "env-a-harness", "version": "1.0.1"},
        }
    ]


def test_submit_eval_by_stream_creates_skill_and_harness_when_base_harness_has_no_skill_refs():
    keys: dict[str, str | dict] = {}
    data_id = "env:env-a@1.0.0"

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
        if request.method == "POST" and path == "/envarena/openapi/v1/streams/source-stream/dataset":
            return httpx.Response(
                200,
                json={
                    "data_ids": [data_id],
                    "items": [{"data_id": data_id, "env_ref": {"key": "env-a", "version": "1.0.0"}}],
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
                    "spec": {
                        "runtime": {"type": "claude_code", "uri": "oss://runtime"},
                        "ttl_seconds": 600,
                    },
                },
            )
        if request.method == "POST" and path == "/envarena/openapi/v1/skills":
            keys["skill"] = str(payload["key"])
            assert keys["skill"].startswith("areal-rsi-source-stream-env-env-a-1.0.0-experience-skill-")
            assert payload["metadata"]["base_harness_ref"] == "base-harness@1.0.0"
            return httpx.Response(201, json={"key": keys["skill"]})
        if request.method == "POST" and path == f"/envarena/openapi/v1/skills/{keys.get('skill')}/versions":
            assert payload["version"] == "1.0.0"
            assert payload["spec"]["bundle"]["uri"] == "inline://areal-rsi/experience-skill"
            assert payload["spec"]["manifest"]["name"] == f"AReaL-RSI experience skill for {data_id}"
            assert payload["spec"]["task_experiences"][0]["experience"] == "new hint"
            return httpx.Response(201, json={"key": keys["skill"], "version": "1.0.0", "spec": payload["spec"]})
        if request.method == "POST" and path == f"/envarena/openapi/v1/skills/{keys.get('skill')}/versions/1.0.0/publish":
            return httpx.Response(200, json={"key": keys["skill"], "version": "1.0.0", "status": "PUBLISHED"})
        if request.method == "POST" and path == "/envarena/openapi/v1/harnesses":
            keys["harness"] = str(payload["key"])
            assert keys["harness"].startswith("areal-rsi-base-harness-env-env-a-1.0.0-experience-harness-")
            assert payload["metadata"]["skill_ref"] == f"{keys['skill']}@1.0.0"
            return httpx.Response(201, json={"key": keys["harness"]})
        if request.method == "POST" and path == f"/envarena/openapi/v1/harnesses/{keys.get('harness')}/versions":
            assert payload["version"] == "1.0.0"
            assert payload["spec"]["runtime"] == {"type": "claude_code", "uri": "oss://runtime"}
            assert payload["spec"]["skill_refs"] == [{"key": keys["skill"], "version": "1.0.0"}]
            assert payload["spec"]["metadata"]["seed"] == "20260806"
            return httpx.Response(201, json={"key": keys["harness"], "version": "1.0.0", "spec": payload["spec"]})
        if request.method == "POST" and path == f"/envarena/openapi/v1/harnesses/{keys.get('harness')}/versions/1.0.0/publish":
            return httpx.Response(200, json={"key": keys["harness"], "version": "1.0.0", "status": "PUBLISHED"})
        if request.method == "POST" and path == "/envarena/openapi/v1/streams/source-stream/launch_batch_task":
            assert payload["model_ref"] == {"key": "model-a", "version": "1.0.0"}
            assert payload["harness_ref"] == {"key": "base-harness", "version": "1.0.0"}
            assert payload["tasks"] == [
                {
                    "data_id": data_id,
                    "harness_ref": {"key": keys["harness"], "version": "1.0.0"},
                }
            ]
            return httpx.Response(
                202,
                json={
                    "accepted": True,
                    "stream_id": "source-stream",
                    "job_id": "job-stream",
                    "task_ids": ["task-1"],
                    "job": _benchmark_job_payload("job-stream", harness_key="base-harness"),
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    job = _client(handler).submit_eval_by_stream(
        {"type": "ref", "ref": "model-a@1.0.0"},
        "source-stream",
        data_ids=[data_id],
        skills=[{"data_id": data_id, "type": "experience", "content": "new hint"}],
        metadata={"seed": 20260806},
    )

    assert job["eval_job_id"] == "job-stream"
    assert job["assets"]["experience_updates"][0]["previous_skill_ref"] is None
    assert job["assets"]["skills"] == [{"key": keys["skill"], "version": "1.0.0"}]
    assert job["assets"]["stream_tasks"] == [
        {
            "data_id": data_id,
            "harness_ref": {"key": keys["harness"], "version": "1.0.0"},
        }
    ]


def test_update_stream_data_skill_experience_requires_skill_key_for_multi_skill_harness():
    data_id = "env:env-a@1.0.0"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/envarena/openapi/v1/streams/source-stream":
            return httpx.Response(
                200,
                json={
                    "stream": {
                        "stream_id": "source-stream",
                        "task": "swe",
                        "default_harness_ref": {"key": "multi-harness", "version": "1.0.0"},
                        "default_reward_ref": {"key": "base-reward", "version": "1.0.0"},
                    }
                },
            )
        if request.method == "POST" and path == "/envarena/openapi/v1/streams/source-stream/dataset":
            return httpx.Response(
                200,
                json={
                    "data_ids": [data_id],
                    "items": [{"data_id": data_id, "env_ref": {"key": "env-a", "version": "1.0.0"}}],
                    "count": 1,
                    "total": 1,
                    "offset": 0,
                    "limit": 1000,
                },
            )
        if request.method == "GET" and path == "/envarena/openapi/v1/harnesses/multi-harness/versions/1.0.0":
            return httpx.Response(
                200,
                json={
                    "key": "multi-harness",
                    "version": "1.0.0",
                    "spec": {
                        "runtime": {"type": "claude_code", "uri": "oss://runtime"},
                        "ttl_seconds": 600,
                        "skill_refs": [
                            {"key": "skill-a", "version": "1.0.0"},
                            {"key": "skill-b", "version": "2.0.0"},
                        ],
                    },
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    with pytest.raises(EnvArenaConfigurationError, match="multiple skill_refs"):
        _client(handler).update_stream_data_skill_experience("source-stream", data_id, {"experience": "new hint"})


def test_update_stream_data_skill_experience_can_target_skill_key_in_multi_skill_harness():
    data_id = "env:env-a@1.0.0"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = json.loads(request.content or b"{}")
        if request.method == "GET" and path == "/envarena/openapi/v1/streams/source-stream":
            return httpx.Response(
                200,
                json={
                    "stream": {
                        "stream_id": "source-stream",
                        "task": "swe",
                        "default_harness_ref": {"key": "multi-harness", "version": "1.0.0"},
                        "default_reward_ref": {"key": "base-reward", "version": "1.0.0"},
                    }
                },
            )
        if request.method == "POST" and path == "/envarena/openapi/v1/streams/source-stream/dataset":
            return httpx.Response(
                200,
                json={
                    "data_ids": [data_id],
                    "items": [{"data_id": data_id, "env_ref": {"key": "env-a", "version": "1.0.0"}}],
                    "count": 1,
                    "total": 1,
                    "offset": 0,
                    "limit": 1000,
                },
            )
        if request.method == "GET" and path == "/envarena/openapi/v1/harnesses/multi-harness/versions/1.0.0":
            return httpx.Response(
                200,
                json={
                    "key": "multi-harness",
                    "version": "1.0.0",
                    "spec": {
                        "runtime": {"type": "claude_code", "uri": "oss://runtime"},
                        "ttl_seconds": 600,
                        "skill_refs": [
                            {"key": "skill-a", "version": "1.0.0"},
                            {"key": "skill-b", "version": "2.0.0"},
                        ],
                    },
                },
            )
        if request.method == "GET" and path == "/envarena/openapi/v1/skills/skill-b/versions/2.0.0":
            return httpx.Response(
                200,
                json={
                    "key": "skill-b",
                    "version": "2.0.0",
                    "spec": {
                        "bundle": {"uri": "oss://skills/skill-b"},
                        "manifest": {"name": "skill-b", "description": "second skill"},
                    },
                },
            )
        if request.method == "POST" and path == "/envarena/openapi/v1/skills/skill-b/versions":
            assert payload["version"] == "2.0.1"
            assert payload["spec"]["task_experiences"][0]["experience"] == "target b"
            return httpx.Response(201, json={"key": "skill-b", "version": "2.0.1", "spec": payload["spec"]})
        if request.method == "POST" and path == "/envarena/openapi/v1/skills/skill-b/versions/2.0.1/publish":
            return httpx.Response(200, json={"key": "skill-b", "version": "2.0.1", "status": "PUBLISHED"})
        if request.method == "POST" and path == "/envarena/openapi/v1/harnesses/multi-harness/versions":
            assert payload["version"] == "1.0.1"
            assert payload["spec"]["skill_refs"] == [
                {"key": "skill-a", "version": "1.0.0"},
                {"key": "skill-b", "version": "2.0.1"},
            ]
            return httpx.Response(201, json={"key": "multi-harness", "version": "1.0.1", "spec": payload["spec"]})
        if request.method == "POST" and path == "/envarena/openapi/v1/harnesses/multi-harness/versions/1.0.1/publish":
            return httpx.Response(200, json={"key": "multi-harness", "version": "1.0.1", "status": "PUBLISHED"})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    result = _client(handler).update_stream_data_skill_experience(
        "source-stream",
        data_id,
        {
            "experience": "target b",
            "skill_key": "skill-b",
            "version": "2.0.1",
            "harness_version": "1.0.1",
        },
    )

    assert result["previous_skill_ref"] == {"key": "skill-b", "version": "2.0.0"}
    assert result["skill_ref"] == {"key": "skill-b", "version": "2.0.1"}
    assert result["harness_ref"] == {"key": "multi-harness", "version": "1.0.1"}
