"""Published CodeV public prompts and response-upload rewards; no model calls."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import httpx

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
ZERO_CATEGORIES = frozenset(
    {
        "wrong_answer",
        "format_error",
        "syntax_error",
        "timeout",
        "invalid_submission",
        "execution_error",
        "invalid_trace",
        "output_limit",
    }
)


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def url(base, path):
    return base.rstrip("/").removesuffix("/openapi/v1") + "/openapi/v1/" + path


def version_path(kind, ref):
    return f"{kind}/{quote(ref['key'], safe='')}/versions/{quote(ref['version'], safe='')}"


def unwrap(value):
    return value.get("data", value)


def public_row(case, entry, config):
    """Explicit public projection: never retain oracle/reference/bundle bodies."""
    ref = entry["case_ref"]
    if case.get("status") != "PUBLISHED" or (case["key"], case["version"]) != (
        ref["key"],
        ref["version"],
    ):
        raise ValueError("Case identity/status mismatch")
    spec = case["spec"]
    meta = spec["metadata"]
    uri = spec["query"]["uri"]
    if not uri.startswith("data:text/markdown;base64,"):
        raise ValueError("Expected the published inline CodeV query contract")
    text = base64.b64decode(uri.split(",", 1)[1], validate=True).decode("utf-8")
    prompt, separator, protocol = text.rpartition("\n\nSubmission protocol:")
    if (
        not separator
        or "No archive or patch." not in protocol
        or not prompt.startswith("system:\n")
    ):
        raise ValueError("Unknown CodeV public prompt protocol")
    system, user = prompt.removeprefix("system:\n").split("\n\nuser:\n", 1)
    messages = [{"content": system, "role": "system"}, {"content": user, "role": "user"}]
    if digest(canonical(messages)) != meta["prompt_sha256"]:
        raise ValueError("Reconstructed original prompt hash mismatch")
    for label in ("harness_ref", "reward_ref"):
        if entry.get(label, config[label]) != config[label]:
            raise ValueError(f"Case {label} differs from the pinned configuration")
    if (
        f"task_id={meta['task_id']}" not in protocol
        or f"baseline_sha256={EMPTY_SHA256}" not in protocol
    ):
        raise ValueError("Unknown task/baseline binding")
    if meta.get("split") != "train":
        raise ValueError("Expected train split")
    return {
        "task_id": meta["task_id"],
        "messages": messages,
        "prompt_sha256": meta["prompt_sha256"],
        "baseline_sha256": EMPTY_SHA256,
        "case_ref": ref,
        "case_content_hash": case["content_hash"],
        "harness_ref": config["harness_ref"],
        "reward_ref": config["reward_ref"],
    }


def prepare_public(config, root):
    """Read-only metadata preparation. No Registry writes, judge jobs, or model calls."""
    headers = {"Authorization": "Bearer " + os.environ["ARENA_OPENAPI_TOKEN"]}
    with httpx.Client(timeout=config["api_timeout"], trust_env=False, headers=headers) as client:

        def get(path):
            for attempt in range(config["read_retries"]):
                try:
                    r = client.get(url(config["base_url"], path))
                    r.raise_for_status()
                    return unwrap(r.json())
                except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                        raise RuntimeError(
                            f"Arena metadata HTTP {exc.response.status_code}"
                        ) from None
                    if attempt + 1 == config["read_retries"]:
                        raise RuntimeError("Arena metadata read failed") from None

        benchmark = get(version_path("benchmarks", config["benchmark_ref"]))
        if (
            benchmark.get("status") != "PUBLISHED"
            or benchmark.get("content_hash") != config["benchmark_content_hash"]
        ):
            raise ValueError("Benchmark status/content hash differs from arena.yaml")
        for kind, name in [("harnesses", "harness_ref"), ("rewards", "reward_ref")]:
            if get(version_path(kind, config[name])).get("status") != "PUBLISHED":
                raise ValueError(f"{name} must be published")
        entries = benchmark["spec"].get("case_refs")
        if not entries or benchmark["spec"].get("reward_ref") != config["reward_ref"]:
            raise ValueError("Expected a Case Benchmark with the pinned reward")

        def read(entry):
            return public_row(get(version_path("cases", entry["case_ref"])), entry, config)

        with ThreadPoolExecutor(max_workers=8) as pool:
            rows = list(pool.map(read, entries))
    if len({r["task_id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate task IDs in Benchmark")
    body = b"".join(canonical(row) + b"\n" for row in rows)
    (root / "public-dataset.jsonl").write_bytes(body)
    return {
        "benchmark_ref": config["benchmark_ref"],
        "benchmark_content_hash": benchmark["content_hash"],
        "count": len(rows),
        "public_dataset_sha256": digest(body),
        "registry_writes": 0,
        "judge_jobs": 0,
        "model_calls": 0,
    }


def validate_verdict(result, task_id, response_sha):
    if result.get("task_id") != task_id or result.get("status") != "OK":
        raise RuntimeError("Arena infrastructure/terminal failure; sample has no reward")
    raw = result.get("raw")
    if not isinstance(raw, dict) or raw.get("response_sha256") != response_sha:
        raise RuntimeError("Arena reward response binding mismatch")
    category = raw.get("category")
    expected = 1 if category == "accepted" else 0 if category in ZERO_CATEGORIES else None
    if expected is None or any(
        type(s) not in (int, float) or not math.isfinite(s) or s != expected
        for s in (result.get("score"), raw.get("score"))
    ):
        raise RuntimeError("Unknown or contradictory Arena judge verdict")
    return float(expected)


class RewardClient:
    def __init__(self, config):
        self.config = config
        self.semaphore = asyncio.Semaphore(config["judge_concurrency"])
        self.scope_locks = {}
        self.scopes = {}

    async def request(
        self, client, method, path, *, body=None, allow_conflict=False, allow_missing=False
    ):
        attempts = self.config["read_retries"] if method == "GET" else 1
        for attempt in range(attempts):
            try:
                r = await client.request(method, url(self.config["base_url"], path), json=body)
                if allow_missing and r.status_code == 404:
                    return None
                if allow_conflict and r.status_code == 409:
                    return None
                r.raise_for_status()
                return unwrap(r.json()) if r.content else {}
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                retryable = (
                    not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code >= 500
                )
                if not retryable or attempt + 1 == attempts:
                    # No request body or authentication fields in exceptions/logs.
                    code = (
                        exc.response.status_code
                        if isinstance(exc, httpx.HTTPStatusError)
                        else type(exc).__name__
                    )
                    raise RuntimeError(
                        f"Arena {method} {path}: {code}; mutation was not retried"
                    ) from None
                await asyncio.sleep(min(2**attempt, 5))

    async def single_scope(self, client, row):
        """Immutable single-Case derivatives prevent grading all 3032 Cases per response."""
        binding = {
            "case_ref": row["case_ref"],
            "harness_ref": row["harness_ref"],
            "reward_ref": row["reward_ref"],
            "weight": 1,
        }
        scope_hash = digest(canonical([self.config["benchmark_content_hash"], binding]))
        key = "codev-upload-" + scope_hash[:32]
        if key in self.scopes:
            return self.scopes[key]
        async with self.scope_locks.setdefault(key, asyncio.Lock()):
            if key in self.scopes:
                return self.scopes[key]
            ref = {"key": key, "version": "1.0.0"}
            path = version_path("benchmarks", ref)
            spec = {
                "case_refs": [binding],
                "reward_ref": row["reward_ref"],
                "aggregation": {"method": "mean", "failed_task_policy": "failed_as_zero"},
                "metadata": {
                    "source_benchmark": self.config["benchmark_ref"]["key"]
                    + "@"
                    + self.config["benchmark_ref"]["version"],
                    "source_content_hash": self.config["benchmark_content_hash"],
                },
            }
            actual = await self.request(client, "GET", path, allow_missing=True)
            if actual is None:
                try:
                    await self.request(
                        client,
                        "POST",
                        "benchmarks",
                        body={"key": key, "name": key},
                        allow_conflict=True,
                    )
                    await self.request(
                        client,
                        "POST",
                        "benchmarks/" + key + "/versions",
                        body={"version": ref["version"], "spec": spec},
                        allow_conflict=True,
                    )
                except RuntimeError:
                    # A concurrent worker may have published the same immutable scope.
                    # Reconcile by reading it; never retry a Registry mutation blindly.
                    if await self.request(client, "GET", path, allow_missing=True) is None:
                        raise
            actual = await self.request(client, "GET", path)
            value = actual.get("spec", {})
            if any(
                value.get(k) != spec[k]
                for k in ("case_refs", "reward_ref", "aggregation", "metadata")
            ):
                raise RuntimeError("Existing single-Case Benchmark scope conflict")
            if actual.get("status") == "DRAFT":
                try:
                    await self.request(
                        client, "POST", path + "/publish", body={}, allow_conflict=True
                    )
                except RuntimeError:
                    # Publication may already have succeeded on another worker.
                    actual = await self.request(client, "GET", path)
                    if actual.get("status") != "PUBLISHED":
                        raise
                actual = await self.request(client, "GET", path)
            if actual.get("status") != "PUBLISHED":
                raise RuntimeError("Single-Case Benchmark is not published")
            self.scopes[key] = ref
            return ref

    async def evaluate(self, row, response, session_id):
        body = response.encode("utf-8")
        if not 0 < len(body) <= self.config["max_response_bytes"]:
            raise RuntimeError("Response exceeds the published 1 MiB transport contract")
        response_sha = digest(body)
        evidence_id = digest(canonical([session_id, row["case_ref"], response_sha]))
        directory = Path(self.config["evidence_dir"]) / evidence_id
        # A repeated local invocation must never create another remote job after an ambiguous POST.
        await asyncio.to_thread(directory.mkdir, parents=True, mode=0o700)
        await asyncio.to_thread((directory / "response.txt").write_bytes, body)
        state = {
            "case_ref": row["case_ref"],
            "response_sha256": response_sha,
            "session_id": session_id,
        }
        headers = {"Authorization": "Bearer " + os.environ["ARENA_OPENAPI_TOKEN"]}
        async with (
            self.semaphore,
            httpx.AsyncClient(
                headers=headers, timeout=self.config["api_timeout"], trust_env=False
            ) as client,
        ):
            job_id = None
            try:
                async with asyncio.timeout(self.config["reward_timeout"]):
                    scope = await self.single_scope(client, row)
                    payload = {
                        "job_id": "codev-" + uuid.uuid4().hex[:24],
                        "benchmark_ref": scope,
                        "harness_ref": row["harness_ref"],
                        "sample_numbers": 1,
                        "model": {
                            "model_name": "uploaded-response-no-inference",
                            "env": {
                                "CODEV_RESPONSE_B64": base64.b64encode(body).decode(),
                                "CODEV_RESPONSE_SHA256": response_sha,
                                "CODEV_TASK_ID": row["task_id"],
                                "CODEV_PROMPT_SHA256": row["prompt_sha256"],
                                "CODEV_BASELINE_SHA256": row["baseline_sha256"],
                            },
                        },
                        "concurrency_policy": {"max_parallel": 1, "max_retries": 0},
                        "tags": ["source:codev-r1", "mode:response-upload"],
                        "metadata": {
                            "experiment": os.environ.get("EXPERIMENT_NAME", "codev")
                            + "-"
                            + os.environ.get("EXPERIMENT_TRIAL", "trial"),
                            "response_sha256": response_sha,
                            "proxy_session_id": session_id,
                        },
                    }
                    state["state"] = "CREATE_INTENT_NO_RETRY"
                    await asyncio.to_thread(
                        (directory / "state.json").write_bytes, canonical(state)
                    )
                    created = await self.request(client, "POST", "jobs", body=payload)
                    job_id = created.get("job", {}).get("job_id")
                    tasks = created.get("task_ids", [])
                    if (
                        not job_id
                        or len(tasks) != 1
                        or created["job"].get("benchmark_ref") != scope
                    ):
                        raise RuntimeError("Arena create must return exactly one bound task")
                    task_id = tasks[0]
                    state.update(job_id=job_id, arena_task_id=task_id, state="JUDGING")
                    await asyncio.to_thread(
                        (directory / "state.json").write_bytes, canonical(state)
                    )
                    while True:
                        actual = await self.request(client, "GET", "jobs/" + quote(job_id, safe=""))
                        tasks = actual.get("tasks", [])
                        if (
                            len(tasks) != 1
                            or tasks[0].get("case_ref") != row["case_ref"]
                            or tasks[0].get("task_id") != task_id
                        ):
                            raise RuntimeError("Actual Arena job changed its Case binding")
                        if actual["job"]["status"] in {
                            "DONE",
                            "FAILED",
                            "CANCELLED",
                            "STOPPED",
                            "TIMEOUT",
                        }:
                            result = await self.request(
                                client, "GET", "tasks/" + quote(task_id, safe="") + "/result"
                            )
                            await asyncio.to_thread(
                                (directory / "result.json").write_bytes, canonical(result)
                            )
                            if actual["job"]["status"] != "DONE":
                                raise RuntimeError("Arena judge job did not complete successfully")
                            reward = validate_verdict(result, task_id, response_sha)
                            state.update(state="VERIFIED", reward=reward)
                            await asyncio.to_thread(
                                (directory / "state.json").write_bytes, canonical(state)
                            )
                            return reward
                        await asyncio.sleep(self.config["poll_interval"])
            except BaseException:
                if job_id is not None:
                    try:
                        await asyncio.shield(
                            self.request(
                                client, "POST", "jobs/" + quote(job_id, safe="") + "/stop", body={}
                            )
                        )
                    except Exception:
                        state["cleanup_failed"] = True
                state["state"] = "FAILED_OR_UNCERTAIN_NO_RETRY"
                await asyncio.to_thread((directory / "state.json").write_bytes, canonical(state))
                raise
