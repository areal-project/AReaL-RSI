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


def _client(handler) -> EnvArenaClient:
    return EnvArenaClient(
        EnvArenaConfig(
            control_base_url="https://arena.example.test/envarena/openapi/v1",
            api_token="test-token",
            gateway_model_alias_prefix="areal-rsi-",
            gateway_inbound_protos=("chat", "responses"),
        ),
        transport=httpx.MockTransport(handler),
    )


def test_config_loads_environment_values_and_normalizes_generated_sdk_base_url(tmp_path):
    config_path = tmp_path / "envarena.yaml"
    config_path.write_text(
        """
envarena:
  control_base_url_env: ARENA_CONTROL_URL
  token_env: ARENA_TOKEN
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
    assert config.gateway_alias_for("ckpt_001") == "areal-rsi-ckpt_001"


def test_gateway_assets_job_progress_result_and_trajectories_are_normalized():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["Authorization"] == "Bearer test-token"
        path = request.url.path
        if request.method == "POST" and path == "/envarena/openapi/v1/llm/models":
            payload = json.loads(request.content)
            assert payload["model_name"] == "areal-rsi-ckpt_001"
            assert payload["endpoints"][0]["base_url"] == "https://areal.example.test/v1"
            assert payload["endpoints"][0]["inbound_protos"] == ["chat", "responses"]
            return httpx.Response(
                201,
                json={
                    "model_name": "areal-rsi-ckpt_001",
                    "enabled": True,
                    "endpoints": [
                        {
                            "endpoint_id": "ep_1",
                            "upstream_model": "ckpt_001",
                            "base_url": "https://areal.example.test/v1",
                            "inbound_protos": ["chat", "responses"],
                            "weight": 1,
                            "enabled": True,
                            "upstream_extra_params": {},
                        }
                    ],
                },
            )
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
    model_ref = client.register_model(
        {
            "model_ref": "ckpt_001",
            "base_url": "https://areal.example.test/v1",
            "api_key": "upstream-secret",
        }
    )

    assert model_ref["gateway_alias"] == "areal-rsi-ckpt_001"
    assert client.list_benchmarks() == [{"key": "swe_verified", "name": "SWE"}]
    assert client.list_benchmark_versions("swe_verified") == [{"key": "swe_verified", "version": "1.0.0"}]

    job = client.submit_eval(EvalRequest(
        model_ref=model_ref,
        task_set="swe_verified@1.0.0",
        harness_config={"harness_ref": {"key": "swe-harness", "version": "1.0.0"}},
        metadata={"run_id": "run_001", "source": "eval"},
    ))

    assert job["eval_job_id"] == "job_001"
    assert client.get_job_summary("job_001")["job"]["status"] == "RUNNING"
    assert client.get_job_result("job_001")["aggregate_score"] == 0.8
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
    assert len(seen) == 8


def test_submit_eval_requires_explicit_published_harness_and_benchmark_refs():
    client = _client(lambda request: pytest.fail(f"Unexpected request: {request.url}"))
    model_ref = {"model_name": "model"}

    with pytest.raises(EnvArenaConfigurationError, match=r"harness_config\['harness_ref'\]"):
        client.submit_eval(EvalRequest(model_ref=model_ref, task_set="benchmark@1.0.0"))

    with pytest.raises(EnvArenaConfigurationError, match="must use 'key@version'"):
        client.submit_eval(EvalRequest(
            model_ref=model_ref,
            task_set="benchmark",
            harness_config={"harness_ref": {"key": "harness", "version": "1.0.0"}},
        ))


def test_http_errors_are_exposed_with_status_code():
    client = _client(lambda request: httpx.Response(403, json={"error": "forbidden"}))

    with pytest.raises(EnvArenaHTTPError) as exc_info:
        client.list_skills()

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == {"error": "forbidden"}


def test_gateway_model_list_can_exclude_dynamic_stream_routes():
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
                        "endpoints": [],
                    }
                ]
            },
        )

    assert _client(handler).list_models() == [
        {"model_name": "areal-rsi-ckpt_001", "enabled": True, "endpoints": []}
    ]


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


def test_update_model_and_submit_eval_preserve_alias_and_encode_runtime_config():
    posted_models: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/envarena/openapi/v1/llm/models":
            payload = json.loads(request.content)
            posted_models.append(payload)
            return httpx.Response(
                201,
                json={
                    "model_name": payload["model_name"],
                    "enabled": True,
                    "endpoints": [
                        {
                            "endpoint_id": "endpoint-1",
                            "upstream_model": payload["endpoints"][0]["upstream_model"],
                            "base_url": payload["endpoints"][0]["base_url"],
                            "inbound_protos": payload["endpoints"][0]["inbound_protos"],
                            "weight": 1,
                            "enabled": True,
                            "upstream_extra_params": {},
                        }
                    ],
                },
            )
        if request.method == "POST" and request.url.path == "/envarena/openapi/v1/jobs":
            payload = json.loads(request.content)
            assert payload == {
                "benchmark_ref": {"key": "benchmark", "version": "2.0.0"},
                "harness_ref": {"key": "harness", "version": "1.2.0"},
                "model": {
                    "model_name": "stable-alias",
                    "parameters": {"temperature": 0.2},
                    "env": {"MODEL_GATEWAY": "https://gateway.example.test"},
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
    updated = client.update_model(
        {"gateway_alias": "stable-alias"},
        {
            "model_ref": "ckpt_002",
            "base_url": "https://areal.example.test/v1",
            "inbound_protos": ["responses"],
            "metadata": {"owner": "rsi"},
        },
    )

    assert updated["gateway_alias"] == "stable-alias"
    assert posted_models[0]["model_name"] == "stable-alias"
    assert posted_models[0]["endpoints"][0]["upstream_model"] == "ckpt_002"
    assert posted_models[0]["metadata"] == {"owner": "rsi"}

    job = client.submit_eval(EvalRequest(
        model_ref={"gateway_alias": "stable-alias"},
        task_set="benchmark@2.0.0",
        harness_config={
            "harness_ref": {"key": "harness", "version": "1.2.0"},
            "job_id": "run-42",
            "sample_numbers": 3,
            "concurrency_policy": {"max_parallel": 4, "max_retries": 2, "retry_backoff_seconds": 5},
            "tags": ["rsi", "holdout"],
            "model_parameters": {"temperature": 0.2},
            "model_env": {"MODEL_GATEWAY": "https://gateway.example.test"},
        },
        metadata={"run_id": 42, "priority": 7},
    ))

    assert job["eval_job_id"] == "run-42-260729"
    assert job["task_ids"] == ["task-1", "task-2"]
    assert job["status"] == "PENDING"


def test_submit_eval_can_reference_a_published_model_without_inline_secrets():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/envarena/openapi/v1/jobs"
        payload = json.loads(request.content)
        assert payload["model_ref"] == {"key": "DeepSeek-V4-Pro", "version": "1.0.2"}
        assert "model" not in payload
        return httpx.Response(202, json={"job_id": "registry-model-smoke", "task_ids": ["task-1"]})

    job = _client(handler).submit_eval(EvalRequest(
        model_ref={"registry_ref": {"key": "DeepSeek-V4-Pro", "version": "1.0.2"}},
        task_set="benchmark@2.0.0",
        harness_config={"harness_ref": "harness@1.2.0"},
    ))

    assert job["model_ref"] == {
        "model_name": "DeepSeek-V4-Pro",
        "gateway_alias": "DeepSeek-V4-Pro",
        "registry_ref": {"key": "DeepSeek-V4-Pro", "version": "1.0.2"},
    }


def test_get_trajs_omits_messages_without_inline_events_and_rollout_is_explicitly_rejected():
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
    with pytest.raises(NotImplementedError, match="no arbitrary offline task rollout"):
        client.submit_rollout({}, [], {}, "eval")


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
