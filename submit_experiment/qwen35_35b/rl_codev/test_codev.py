"""Offline contracts: public projection, one proxy call, scoped upload, verified reward."""

import base64
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import codev  # noqa: E402

spec = importlib.util.spec_from_file_location("codev_submit", ROOT / "submit.py")
submit = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = submit
spec.loader.exec_module(submit)


def config(tmp_path):
    value = yaml.safe_load((ROOT / "params.yaml").read_text())["codev"]
    value.update(yaml.safe_load((ROOT / "arena.yaml").read_text())["codev"])
    return {**value, "base_url": "http://arena", "evidence_dir": str(tmp_path)}


def fixture_row(c):
    messages = [
        {"content": "Think then answer.\n", "role": "system"},
        {"content": "Make a circuit.", "role": "user"},
    ]
    sha = codev.digest(codev.canonical(messages))
    text = "system:\n" + messages[0]["content"] + "\n\nuser:\n" + messages[1]["content"]
    text += (
        "\n\nSubmission protocol: task_id=train-1 baseline_sha256="
        + codev.EMPTY_SHA256
        + " No archive or patch."
    )
    entry = {"case_ref": {"key": "case1", "version": "1.0.0"}}
    case = {
        "key": "case1",
        "version": "1.0.0",
        "status": "PUBLISHED",
        "content_hash": "abc",
        "spec": {
            "query": {
                "uri": "data:text/markdown;base64," + base64.b64encode(text.encode()).decode()
            },
            "metadata": {"task_id": "train-1", "prompt_sha256": sha, "split": "train"},
            "oracle": {"uri": "DO_NOT_PROJECT_ORACLE"},
        },
    }
    return case, entry


def test_public_prompt_hash_and_oracle_projection(tmp_path):
    c = config(tmp_path)
    case, entry = fixture_row(c)
    row = codev.public_row(case, entry, c)
    assert "oracle" not in json.dumps(row)
    assert codev.digest(codev.canonical(row["messages"])) == row["prompt_sha256"]
    case["spec"]["metadata"]["prompt_sha256"] = "bad"
    with pytest.raises(ValueError, match="hash"):
        codev.public_row(case, entry, c)


@pytest.mark.parametrize(
    "category,score", [("accepted", 1), ("wrong_answer", 0), ("syntax_error", 0), ("timeout", 0)]
)
def test_candidate_verdicts(category, score):
    r = {
        "task_id": "task",
        "status": "OK",
        "score": score,
        "raw": {"category": category, "score": score, "response_sha256": "abc"},
    }
    assert codev.validate_verdict(r, "task", "abc") == score


@pytest.mark.parametrize("fault", ["status", "hash", "score", "category"])
def test_infrastructure_and_conflicting_rewards_are_not_zero(fault):
    r = {
        "task_id": "task",
        "status": "OK",
        "score": 0,
        "raw": {"category": "wrong_answer", "score": 0, "response_sha256": "abc"},
    }
    if fault == "status":
        r["status"] = "EVAL_FAILED"
    elif fault == "hash":
        r["raw"]["response_sha256"] = "wrong"
    elif fault == "score":
        r["score"] = 1
    else:
        r["raw"]["category"] = "internal_error"
    with pytest.raises(RuntimeError):
        codev.validate_verdict(r, "task", "abc")


@pytest.mark.asyncio
async def test_single_turn_uses_real_openai_proxy_protocol_once(monkeypatch, tmp_path):
    import codev_agent

    c = config(tmp_path)
    case, entry = fixture_row(c)
    row = codev.public_row(case, entry, c)
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "id": "one",
                "object": "chat.completion",
                "created": 1,
                "model": "default",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "reasoning_content": "reasoning",
                            "content": "<answer>```verilog\nmodule a; endmodule\n```</answer>",
                        },
                    }
                ],
            },
        )

    agent = codev_agent.CodeVAgent(
        c, {"temperature": 1, "top_p": 1, "top_k": 1000000, "max_completion_tokens": 65536}
    )
    agent.rewards.evaluate = AsyncMock(return_value=1.0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        assert (
            await agent.run(
                row,
                base_url="http://proxy",
                api_key="session-key",
                http_client=client,
                session_id="session",
            )
            == 1
        )
    assert len(seen) == 1
    payload = json.loads(seen[0].content)
    assert (
        payload["n"] == 1 and payload["messages"] == row["messages"] and payload["top_k"] == 1000000
    )
    assert seen[0].headers["authorization"] == "Bearer session-key"
    args = agent.rewards.evaluate.call_args.args
    assert (
        args[0] == row
        and args[1].startswith("<think>\nreasoning</think>\n<answer>")
        and args[2] == "session"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrent_publish", [False, True])
async def test_judge_submission_is_one_case_and_response_bound(
    monkeypatch, tmp_path, concurrent_publish
):
    c = config(tmp_path)
    case, entry = fixture_row(c)
    row = codev.public_row(case, entry, c)
    worker = codev.RewardClient(c)
    calls = []
    saved = {}

    async def request(client, method, path, *, body=None, **kwargs):
        calls.append((method, path, body))
        if method == "POST" and path.endswith("/versions"):
            saved["spec"] = body["spec"]
            if concurrent_publish:
                raise RuntimeError("Concurrent immutable version already exists")
            return {}
        if method == "GET" and path.startswith("benchmarks/"):
            return {"status": "PUBLISHED", "spec": saved["spec"]} if "spec" in saved else None
        if method == "POST" and path == "jobs":
            saved["payload"] = body
            return {
                "job": {"job_id": "job", "benchmark_ref": body["benchmark_ref"]},
                "task_ids": ["task"],
            }
        if method == "GET" and path == "jobs/job":
            return {
                "job": {"status": "DONE"},
                "tasks": [{"task_id": "task", "case_ref": row["case_ref"]}],
            }
        if method == "GET" and path == "tasks/task/result":
            return {
                "task_id": "task",
                "status": "OK",
                "score": 0,
                "raw": {
                    "category": "syntax_error",
                    "score": 0,
                    "response_sha256": codev.digest(b"candidate"),
                },
            }
        return {}

    monkeypatch.setenv("ARENA_OPENAPI_TOKEN", "test-token")
    monkeypatch.setattr(worker, "request", request)
    assert await worker.evaluate(row, "candidate", "session") == 0
    assert len(saved["spec"]["case_refs"]) == 1
    assert saved["spec"]["case_refs"][0]["case_ref"] == row["case_ref"]
    env = saved["payload"]["model"]["env"]
    assert base64.b64decode(env["CODEV_RESPONSE_B64"]) == b"candidate"
    assert env["CODEV_TASK_ID"] == row["task_id"]
    assert len([c for c in calls if c[:2] == ("POST", "jobs")]) == 1
    with pytest.raises(FileExistsError):
        await worker.evaluate(row, "candidate", "session")
    assert len([c for c in calls if c[:2] == ("POST", "jobs")]) == 1


@pytest.mark.asyncio
async def test_mutation_transport_failure_is_not_retried(tmp_path):
    seen = []

    def fail(request):
        seen.append(request)
        raise httpx.ReadTimeout("lost response")

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(RuntimeError, match="not retried"):
            await codev.RewardClient(config(tmp_path)).request(
                client, "POST", "jobs", body={"x": 1}
            )
    assert len(seen) == 1


def test_profile_has_fixed_topology_and_native_capacity(tmp_path):
    c = submit.compose(ROOT, ROOT / "params.yaml", ROOT / "arena.yaml")
    assert c["codev"]["benchmark_ref"]["version"] == "1.0.3"
    assert "econfig" not in c and c["rollout"]["max_concurrent_rollouts"] == 32
    assert c["gconfig"]["n_samples"] == 12 and c["actor"]["min_usable_group_size"] == 8
    assert (
        c["sglang"]["max_running_requests"]
        == "${mul:${rollout.max_concurrent_rollouts},${gconfig.n_samples}}"
    )
    assert c["actor"]["megatron"]["cpu_staged_offload"]["enabled"]
    assert c["actor"]["megatron"]["enable_chunked_logits"]
