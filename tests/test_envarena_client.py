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
            model_instance_name_prefix="areal-rsi-",
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
    assert config.model_instance_name_prefix == "areal-rsi-"


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
        dataset={"type": "ref", "ref": "swe_verified@1.0.0"},
        harness={"type": "ref", "ref": {"key": "swe-harness", "version": "1.0.0"}},
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

    with pytest.raises(EnvArenaConfigurationError, match="harnesses ref assets require"):
        client.submit_eval(EvalRequest(
            model={"type": "ref", "model_name": "model"},
            dataset={"type": "ref", "ref": "benchmark@1.0.0"},
            harness={"type": "ref"},
        ))

    with pytest.raises(EnvArenaConfigurationError, match="benchmarks ref assets require"):
        client.submit_eval(EvalRequest(
            model={"type": "ref", "model_name": "model"},
            dataset={"type": "ref", "ref": "benchmark"},
            harness={"type": "ref", "ref": {"key": "harness", "version": "1.0.0"}},
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
                    }
                ]
            },
        )

    assert _client(handler).list_models() == [
        {"model_name": "areal-rsi-ckpt_001", "enabled": True}
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
        dataset={"type": "ref", "ref": "benchmark@2.0.0"},
        harness={"type": "ref", "ref": {"key": "harness", "version": "1.2.0"}},
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
        dataset={"type": "ref", "ref": "benchmark@2.0.0"},
        harness={"type": "ref", "ref": "harness@1.2.0"},
    ))

    assert job["model_ref"] == {
        "model_name": "DeepSeek-V4-Pro",
        "runtime_model_name": "DeepSeek-V4-Pro",
        "registry_ref": {"key": "DeepSeek-V4-Pro", "version": "1.0.2"},
    }


def test_submit_eval_can_create_new_registry_assets_for_dataset_harness_skill_and_reward():
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
        if path == "/envarena/openapi/v1/rewards":
            assert payload["key"] == "reward-rsi"
            return httpx.Response(201, json={})
        if path == "/envarena/openapi/v1/rewards/reward-rsi/versions":
            assert payload == {"version": "1.0.0", "spec": {"kind": "python"}}
            return httpx.Response(201, json={})
        if path == "/envarena/openapi/v1/rewards/reward-rsi/versions/1.0.0/publish":
            return httpx.Response(200, json={})
        if path == "/envarena/openapi/v1/benchmarks":
            assert payload["key"] == "dataset-rsi"
            return httpx.Response(201, json={})
        if path == "/envarena/openapi/v1/benchmarks/dataset-rsi/versions":
            assert payload["spec"]["reward_ref"] == {"key": "reward-rsi", "version": "1.0.0"}
            return httpx.Response(201, json={})
        if path == "/envarena/openapi/v1/benchmarks/dataset-rsi/versions/1.0.0/publish":
            return httpx.Response(200, json={})
        if path == "/envarena/openapi/v1/harnesses":
            assert payload["key"] == "harness-rsi"
            return httpx.Response(201, json={})
        if path == "/envarena/openapi/v1/harnesses/harness-rsi/versions":
            assert payload["spec"]["skill_refs"] == [{"key": "skill-rsi", "version": "1.0.0"}]
            return httpx.Response(201, json={})
        if path == "/envarena/openapi/v1/harnesses/harness-rsi/versions/1.0.0/publish":
            return httpx.Response(200, json={})
        if path == "/envarena/openapi/v1/jobs":
            assert payload["benchmark_ref"] == {"key": "dataset-rsi", "version": "1.0.0"}
            assert payload["harness_ref"] == {"key": "harness-rsi", "version": "1.0.0"}
            assert payload["model"] == {"model_name": "stable-alias"}
            return httpx.Response(202, json={"job_id": "job-created-assets", "task_ids": []})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    job = _client(handler).submit_eval(EvalRequest(
        model={"type": "ref", "model_name": "stable-alias"},
        dataset={
            "type": "new",
            "key": "dataset-rsi",
            "version": "1.0.0",
            "name": "RSI Dataset",
            "spec": {"env_refs": []},
        },
        harness={
            "type": "new",
            "key": "harness-rsi",
            "version": "1.0.0",
            "name": "RSI Harness",
            "spec": {"agent": {"kind": "mock"}},
        },
        skill={
            "type": "new",
            "key": "skill-rsi",
            "version": "1.0.0",
            "name": "RSI Skill",
            "spec": {"manifest": {"name": "skill-rsi"}},
        },
        reward={
            "type": "new",
            "key": "reward-rsi",
            "version": "1.0.0",
            "name": "RSI Reward",
            "spec": {"kind": "python"},
        },
    ))

    assert job["eval_job_id"] == "job-created-assets"
    assert job["assets"]["skill"] == {"key": "skill-rsi", "version": "1.0.0"}
    assert job["assets"]["reward"] == {"key": "reward-rsi", "version": "1.0.0"}
    assert len(seen) == 13


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
