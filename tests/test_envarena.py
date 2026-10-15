"""Offline Arena contract and recovery tests. Live smoke evidence is kept separately."""

from __future__ import annotations

import asyncio
import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, unquote, urlsplit

from areal_rsi.api.records import (
    BenchmarkSplit,
    ContentRef,
    HarnessChange,
    VerificationRequest,
    VerificationResult,
    VerificationStatus,
    VerifierSpec,
)
from areal_rsi.infra.storage.files import (
    FileContentStore,
    FileTrajectoryStore,
    FileVersionManager,
    decode,
)
from areal_rsi.infra.tasks.remote.envarena.benchmark import ArenaBenchmark
from areal_rsi.infra.tasks.remote.envarena.client import (
    ArenaClient,
    ArenaConnection,
    ArenaError,
    fingerprint,
    redact,
)
from areal_rsi.infra.tasks.remote.envarena.config import ArenaConfig
from areal_rsi.infra.tasks.remote.envarena.harness import (
    REMOTE_CHANGE_TYPE,
    ArenaHarnessBuilder,
    snapshot,
    validate_change,
)
from areal_rsi.infra.tasks.remote.envarena.runtime import (
    AmbiguousSubmission,
    ArenaEvaluation,
    ArenaHarnessRuntime,
    ArenaVerifier,
)

REF = {"key": "base", "version": "1.0.0"}


class FakeArena(ArenaClient):
    def __init__(self) -> None:
        super().__init__(ArenaConnection("https://arena.example/openapi/v1", "secret-test-only"))
        self.entities: dict[str, dict[str, Any]] = {}
        self.versions: dict[str, dict[str, Any]] = {}
        self.jobs: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str, Any]] = []
        self.lost_response = False
        self.unaccepted = False
        self.running = False
        self.ready = asyncio.Event()
        self.fail_case: str | None = None
        self.score = 0.5
        self.wrong_harness = False
        self.uploads: list[tuple[str, bytes]] = []
        self.artifact_downloads: list[tuple[str, str, int]] = []
        self.artifact_listing: Any = {
            "items": [
                {
                    "name": "result.py",
                    "role": "harness",
                    "attempt": 1,
                    "size_bytes": 16,
                    "type": "text/x-python",
                },
                {
                    "name": "oracle.py",
                    "role": "reward",
                    "attempt": 1,
                    "size_bytes": 16,
                    "type": "text/x-python",
                },
            ]
        }
        self.seed(
            "harnesses",
            "base",
            {
                "runtime": {"type": "runner", "uri": "remote/runtime"},
                "agent": {"env": {"FIXED": "yes"}},
                "ttl_seconds": 600,
                "io_contract": {"artifact_globs": ["$ARENA_AGENT_OUTPUT_DIR/**/*"]},
                "system_prompt": {"kind": "inline", "content": "baseline prompt"},
            },
        )
        self.seed(
            "models", "base", {"model_name": "remote-model", "env": {"API_KEY": "secret-test-only"}}
        )
        self.seed("rewards", "base", {"normalization": ".score"})
        self.seed(
            "benchmarks",
            "base",
            {
                "case_refs": [
                    {"case_ref": {"key": key, "version": "1.0.0"}, "weight": weight}
                    for key, weight in (("train-a", 1), ("train-b", 3), ("validate", 1))
                ],
                "reward_ref": REF,
                "aggregation": {"method": "mean", "failed_task_policy": "failed_as_zero"},
            },
        )
        for key in ("train-a", "train-b", "validate"):
            self.seed(
                "cases",
                key,
                {
                    "query": {"uri": "remote/public-query"},
                    "env_ref": REF,
                    "oracle": {"uri": "DO-NOT-LEAK-ORACLE"},
                },
            )

    def seed(self, kind: str, key: str, spec: dict[str, Any]) -> None:
        self.entities[f"/{kind}/{key}"] = {"key": key, "tags": []}
        self.versions[f"/{kind}/{key}/versions/1.0.0"] = {
            "key": key,
            "version": "1.0.0",
            "status": "PUBLISHED",
            "spec": copy.deepcopy(spec),
            "content_hash": fingerprint(spec),
        }

    async def post_file(
        self, path: str, filename: str, content: bytes, fields: dict[str, str] | None = None
    ) -> Any:
        self.uploads.append((path, content))
        if path == "/skills/import":
            assert fields is not None
            self.seed("skills", fields["key"], {"bundle": {"uri": "uploaded-skill"}})
            return self.versions[f"/skills/{fields['key']}/versions/1.0.0"]
        return {"uri": f"uploads/{len(self.uploads)}/{filename}"}

    async def artifact(self, task_id: str, name: str, attempt: int) -> bytes:
        assert name == "result.py" and attempt == 1
        self.artifact_downloads.append((task_id, name, attempt))
        return b"generated source"

    async def request(self, method: str, path: str, body: Any = None) -> Any:
        self.calls.append((method, path, copy.deepcopy(body)))
        url = urlsplit(path)
        route = unquote(url.path)
        args = parse_qs(url.query)
        if method == "GET":
            if route in self.versions:
                return copy.deepcopy(self.versions[route])
            if route in self.entities:
                return copy.deepcopy(self.entities[route])
            if route == "/jobs":
                tag = args.get("tag", [""])[0]
                return {"jobs": [copy.deepcopy(j) for j in self.jobs.values() if tag in j["tags"]]}
            if route.startswith("/jobs/"):
                job = self.jobs[route.split("/")[2]]
                if route.endswith("/summary"):
                    self.ready.set()
                    return {"job": copy.deepcopy(job), "task_counts": {"total": len(job["tasks"])}}
                if route.endswith("/tasks"):
                    rows = copy.deepcopy(job["tasks"])
                    if self.wrong_harness:
                        rows[0]["harness_ref"] = {"key": "wrong", "version": "1"}
                    return {"items": rows, "total": len(rows)}
                if route.endswith("/result"):
                    scores = [0 if t["status"] != "DONE" else self.score for t in job["tasks"]]
                    spec = self.versions[
                        f"/benchmarks/{job['benchmark_ref']['key']}/versions/1.0.0"
                    ]["spec"]
                    weights = [e.get("weight", 1) for e in spec["case_refs"]]
                    return {
                        "aggregate_score": sum(s * w for s, w in zip(scores, weights, strict=True))
                        / sum(weights)
                    }
                if route.endswith("/trajectory"):
                    tid = route.split("/tasks/")[1].removesuffix("/trajectory")
                    return {
                        "task_id": tid,
                        "attempt": 1,
                        "events": [
                            {"type": "system", "system_prompt": "actual prompt"},
                            {
                                "type": "user",
                                "message": {"role": "user", "content": "public question"},
                            },
                            {
                                "type": "assistant",
                                "message": {"role": "assistant", "content": "actual answer"},
                            },
                            {
                                "type": "result",
                                "usage": {"prompt_tokens": 20, "completion_tokens": 5},
                            },
                        ],
                    }
            if route.startswith("/tasks/"):
                tid = route[len("/tasks/") :].rsplit("/", 1)[0]
                if route.endswith("/artifacts"):
                    return copy.deepcopy(self.artifact_listing)
                task = next(
                    t for j in self.jobs.values() for t in j["tasks"] if t["task_id"] == tid
                )
                return {
                    "task_id": tid,
                    "score": 0 if task["status"] != "DONE" else self.score,
                    "status": "OK",
                    "raw": {},
                    "computed_at": "2026-09-08T00:00:00Z",
                }
            raise ArenaError(404, method, path)
        if method == "POST":
            if route == "/jobs":
                if self.unaccepted:
                    raise ArenaError(0, method, path)
                assert isinstance(body, dict)
                job_id = f"job-{len(self.jobs) + 1}"
                benchmark = self.versions[
                    f"/benchmarks/{body['benchmark_ref']['key']}/versions/1.0.0"
                ]["spec"]
                tasks = [
                    {
                        "task_id": f"{job_id}:{e['case_ref']['key']}:0",
                        "case_ref": e["case_ref"],
                        "harness_ref": body["harness_ref"],
                        "model_ref": body["model_ref"],
                        "reward_ref": benchmark["reward_ref"],
                        "sample_index": 0,
                        "attempts": 1,
                        "status": "HARNESS_FAILED"
                        if e["case_ref"]["key"] == self.fail_case
                        else "DONE",
                        "updated_at": "2026-09-08T00:00:00Z",
                    }
                    for e in benchmark["case_refs"]
                ]
                self.jobs[job_id] = {
                    **copy.deepcopy(body),
                    "job_id": job_id,
                    "tasks": tasks,
                    "status": "RUNNING" if self.running else "DONE",
                }
                if self.lost_response:
                    self.lost_response = False
                    raise ArenaError(0, method, path)
                return {"job_id": job_id, "task_ids": [t["task_id"] for t in tasks]}
            if route.endswith("/stop"):
                self.jobs[route.split("/")[2]]["status"] = "STOPPED"
                return {}
            if route.endswith("/start"):
                self.jobs[route.split("/")[2]]["status"] = "DONE"
                return {}
            if route.endswith("/publish"):
                self.versions[route.removesuffix("/publish")]["status"] = "PUBLISHED"
                return {}
            if route.endswith("/versions"):
                self.versions[route + "/1.0.0"] = {
                    **copy.deepcopy(body),
                    "key": route.split("/")[2],
                    "status": "DRAFT",
                    "content_hash": fingerprint(body["spec"]),
                }
                return self.versions[route + "/1.0.0"]
            self.entities[route + "/" + body["key"]] = copy.deepcopy(body)
            return body
        raise AssertionError((method, path))


class RemoteTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.client = FakeArena()
        self.config = ArenaConfig(
            REF,
            REF,
            REF,
            ("train-a", "train-b"),
            ("validate",),
            ("system_prompt", "skills", "tools", "mcp"),
            poll_seconds=0.01,
        )
        self.content = FileContentStore(self.root / "content")
        self.versions = FileVersionManager(self.root / "versions")
        self.traces = FileTrajectoryStore(self.root / "trajectories", self.content)
        self.benchmark = ArenaBenchmark(self.client, self.config)
        await self.benchmark.load()
        model = await self.versions.create_model(
            "remote-model",
            await self.content.put_json({"ref": {"key": "base", "version": "1.0.0"}}),
        )
        harness = await self.versions.create_harness(
            "remote", await snapshot(self.client, self.content, REF)
        )
        self.solution = await self.versions.create_solution(model, harness)
        self.builder = ArenaHarnessBuilder(self.client, self.content, self.config)
        self.runtime = self.new_runtime()
        self.verifier = ArenaVerifier(self.content)

    def new_runtime(self) -> ArenaHarnessRuntime:
        return ArenaHarnessRuntime(
            self.client,
            self.content,
            self.traces,
            self.benchmark,
            self.config,
            self.root / "remote",
        )

    async def execute(self, key: str = "run/train") -> Any:
        return await self.runtime.run_many(
            self.solution,
            await self.benchmark.list_tasks(BenchmarkSplit.TRAIN),
            config={"execution_key": key},
        )

    async def test_batch_reconnect_and_real_scores_preserve_case_identity(self) -> None:
        self.client.fail_case = "train-b"
        first = await self.execute()
        self.runtime = self.new_runtime()
        second = await self.execute()
        self.assertEqual(first, second)
        self.assertEqual(len(self.client.jobs), 1)
        tasks = await self.benchmark.list_tasks(BenchmarkSplit.TRAIN)
        results = [
            await self.verifier.verify(
                VerificationRequest(
                    t, self.solution, first[t.task_id], VerifierSpec("arena", "injected")
                )
            )
            for t in tasks
        ]
        report = await ArenaEvaluation().evaluate(self.solution, BenchmarkSplit.TRAIN, results)
        self.assertEqual(report.score, 0.125)  # 0.5*1 + failed*3, denominator=4
        self.assertEqual(report.metrics["failed_count"], 1)
        for result in first.values():
            trace = await self.traces.get(result.trajectory.trajectory_id)
            assert trace is not None
            self.assertEqual(trace.usage["prompt_tokens"], 20)
            text = await self.content.read_text(trace.content)
            self.assertIn("actual answer", text)
            self.assertNotIn("oracle.py", text)
        self.assertNotIn("DO-NOT-LEAK-ORACLE", json.dumps([t.metadata for t in tasks]))

    async def test_lost_submission_response_is_reconciled_without_duplicate(self) -> None:
        self.client.lost_response = True
        with self.assertRaises(ArenaError):
            await self.execute()
        self.runtime = self.new_runtime()
        await self.execute()
        self.assertEqual(len(self.client.jobs), 1)
        self.assertEqual(sum(m == "POST" and p == "/jobs" for m, p, _ in self.client.calls), 1)

    async def test_artifact_envelopes_download_only_current_harness_outputs(self) -> None:
        rows = self.client.artifact_listing["items"]
        rows.append({"name": "previous.py", "role": "harness", "attempt": 0, "size_bytes": 16})
        for index, listing in enumerate(({"items": rows}, {"artifacts": rows}, rows)):
            with self.subTest(envelope=index):
                self.client.artifact_listing = listing
                results = await self.execute(f"artifact-envelope-{index}")
                for result in results.values():
                    output = await self.content.read_json(result.output)
                    assert isinstance(output, dict) and isinstance(output["arena"], dict)
                    evidence = output["arena"]
                    attachments = evidence["artifact_refs"]
                    assert isinstance(attachments, list) and len(attachments) == 1
                    attachment = attachments[0]
                    assert isinstance(attachment, dict)
                    self.assertEqual(attachment["name"], "result.py")
                    self.assertEqual(
                        await self.content.read_bytes(decode(ContentRef, attachment["content"])),
                        b"generated source",
                    )
        self.assertEqual(len(self.client.artifact_downloads), 6)

    async def test_unknown_artifact_envelope_is_not_silently_treated_as_empty(self) -> None:
        self.client.artifact_listing = {"unexpected": []}
        with self.assertRaisesRegex(ValueError, "产物列表"):
            await self.execute()

    async def test_configurable_artifacts_become_verifier_attachments(self) -> None:
        self.config = replace(self.config, artifact_globs=("*.js", "*.txt"))
        self.runtime = self.new_runtime()
        self.client.artifact_listing["items"].extend(
            [
                {"name": "model.js", "role": "harness", "attempt": 1, "size_bytes": 16},
                {"name": "old.js", "role": "harness", "attempt": 0, "size_bytes": 16},
                {"name": "private.js", "role": "reward", "attempt": 1, "size_bytes": 16},
                {"name": "huge.txt", "role": "harness", "attempt": 1, "size_bytes": 3_000_000},
            ]
        )
        with patch.object(
            self.client, "artifact", AsyncMock(return_value=b"generated source")
        ) as get:
            results = await self.execute()
        self.assertEqual([call.args[1:] for call in get.call_args_list], [("model.js", 1)] * 2)
        for task in await self.benchmark.list_tasks(BenchmarkSplit.TRAIN):
            verified = await self.verifier.verify(
                VerificationRequest(
                    task, self.solution, results[task.task_id], VerifierSpec("arena", "injected")
                )
            )
            self.assertEqual(len(verified.attachments), 1)
            self.assertEqual(
                await self.content.read_bytes(verified.attachments[0]), b"generated source"
            )

    async def test_artifact_path_and_actual_size_are_checked(self) -> None:
        for name, size, data in (("../escape.py", 16, b"source"), ("ok.py", 16, b"x" * 3_000_000)):
            with self.subTest(name=name):
                self.client.artifact_listing = [
                    {"name": name, "role": "harness", "attempt": 1, "size_bytes": size}
                ]
                with (
                    patch.object(self.client, "artifact", AsyncMock(return_value=data)),
                    self.assertRaises(ValueError),
                ):
                    await self.execute(name)

    async def test_unknown_submission_with_no_job_is_not_reposted(self) -> None:
        self.client.unaccepted = True
        with self.assertRaises(ArenaError):
            await self.execute()
        with self.assertRaises(AmbiguousSubmission):
            await self.execute()
        self.assertEqual(sum(m == "POST" and p == "/jobs" for m, p, _ in self.client.calls), 1)

    async def test_stop_remote_job_and_resume_same_job(self) -> None:
        self.client.running = True
        task = asyncio.create_task(self.execute())
        await asyncio.wait_for(self.client.ready.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.client.jobs["job-1"]["status"], "STOPPED")
        self.runtime = self.new_runtime()
        await self.execute()
        self.assertEqual(len(self.client.jobs), 1)
        self.assertEqual(self.client.jobs["job-1"]["status"], "DONE")

    async def test_cancel_before_submission_is_safe_to_resume(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def prepare(tasks: Any) -> dict[str, str]:
            started.set()
            await release.wait()
            return REF

        with patch.object(self.benchmark, "ensure_subset", side_effect=prepare):
            task = asyncio.create_task(self.execute())
            await asyncio.wait_for(started.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(self.client.jobs)
        await self.execute()
        self.assertEqual(len(self.client.jobs), 1)

    async def test_two_runtime_instances_cannot_submit_the_same_execution_concurrently(
        self,
    ) -> None:
        self.client.running = True
        task = asyncio.create_task(self.execute())
        await asyncio.wait_for(self.client.ready.wait(), 2)
        other = self.new_runtime()
        with self.assertRaisesRegex(RuntimeError, "另一个执行器"):
            await other.run_many(
                self.solution,
                await self.benchmark.list_tasks(BenchmarkSplit.TRAIN),
                config={"execution_key": "run/train"},
            )
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(self.client.jobs), 1)

    async def test_external_retry_generation_cannot_reuse_old_results(self) -> None:
        await self.execute()
        self.client.jobs["job-1"]["execution_generation"] = 2
        with self.assertRaisesRegex(ValueError, "generation"):
            await self.execute()

    async def test_execution_key_cannot_be_reused_for_different_input(self) -> None:
        await self.execute()
        with self.assertRaisesRegex(ValueError, "不同配置"):
            await self.runtime.run_many(
                self.solution,
                await self.benchmark.list_tasks(BenchmarkSplit.VALIDATE),
                config={"execution_key": "run/train"},
            )

    async def test_task_binding_mismatch_fails(self) -> None:
        self.client.wrong_harness = True
        with self.assertRaisesRegex(ValueError, "harness_ref"):
            await self.execute()

    async def test_remote_builder_changes_only_allowed_configuration(self) -> None:
        change = HarnessChange(
            await self.content.put_bytes(
                b'{"system_prompt":"new prompt"}', media_type=REMOTE_CHANGE_TYPE
            ),
            "reason",
        )
        candidate = await self.builder.build(self.solution.harness, change)
        again = await self.builder.build(self.solution.harness, change)
        self.assertEqual(candidate, again)
        saved = json.loads(await self.content.read_text(candidate))
        original = self.client.versions["/harnesses/base/versions/1.0.0"]["spec"]
        for field in ("runtime", "agent", "ttl_seconds", "io_contract"):
            self.assertEqual(saved["spec"][field], original[field])
        self.assertEqual(saved["spec"]["system_prompt"]["content"], "new prompt")
        self.assertEqual(original["system_prompt"]["content"], "baseline prompt")
        self.assertFalse(any("upload" in path for _, path, _ in self.client.calls))

    async def test_prompt_append_preserves_original_contract(self) -> None:
        change = HarnessChange(
            await self.content.put_bytes(
                b'{"system_prompt":{"append":"Additional guidance"}}',
                media_type=REMOTE_CHANGE_TYPE,
            ),
            "test",
        )
        candidate = await self.builder.build(self.solution.harness, change)
        saved = json.loads(await self.content.read_text(candidate))
        self.assertEqual(
            saved["spec"]["system_prompt"]["content"], "baseline prompt\n\nAdditional guidance"
        )
        invalid: tuple[Any, ...] = ({"append": ""}, {"append": "text", "replace": "text"}, [])
        for bad in invalid:
            with self.assertRaises(ValueError):
                validate_change({"system_prompt": bad}, self.config.allowed_changes)

    async def test_skill_tool_and_mcp_candidates_use_remote_apis_and_reuse_uploads(self) -> None:
        value = {
            "skills": [
                {"name": "checks", "description": "General checks", "content": "Read real errors."}
            ],
            "tools": [
                {
                    "name": "remote-service",
                    "description": "Remote MCP",
                    "mcp_config": {"mcpServers": {"service": {"url": "https://mcp.example/mcp"}}},
                }
            ],
            "mcp": {"mcpServers": {}},
        }
        change = HarnessChange(
            await self.content.put_bytes(json.dumps(value).encode(), media_type=REMOTE_CHANGE_TYPE),
            "test",
        )
        first = await self.builder.build(self.solution.harness, change)
        second = await self.builder.build(self.solution.harness, change)
        self.assertEqual(first, second)
        self.assertEqual(len(self.client.uploads), 2)  # Skill Markdown + MCP JSON, never a runtime.
        self.assertIn(b'name: "checks"', self.client.uploads[0][1])
        saved = json.loads(await self.content.read_text(first))["spec"]
        self.assertEqual(len(saved["skill_refs"]), 1)
        self.assertEqual(len(saved["tool_refs"]), 1)
        self.assertIn("mcp_config", saved["materials"])

    async def test_draft_content_tampering_is_not_published(self) -> None:
        spec = {"runtime": {"type": "runner", "uri": "fixed"}, "ttl_seconds": 600}
        ref = await self.client.ensure_version("harnesses", "test", spec)
        saved = self.client.versions[f"/harnesses/{ref['key']}/versions/1.0.0"]
        saved["status"] = "DRAFT"
        saved["spec"]["metadata"]["rsi_spec_sha256"] = "different"
        with self.assertRaisesRegex(ValueError, "草稿内容"):
            await self.client.ensure_version("harnesses", "test", spec)

    async def test_draft_body_is_verified_even_with_the_original_digest_label(self) -> None:
        spec = {"runtime": {"type": "runner", "uri": "fixed"}, "ttl_seconds": 600}
        ref = await self.client.ensure_version("harnesses", "test", spec)
        self.assertEqual(await self.client.ensure_version("harnesses", "test", spec), ref)
        saved = self.client.versions[f"/harnesses/{ref['key']}/versions/1.0.0"]
        saved["spec"]["runtime"]["uri"] = "tampered"
        saved["status"] = "DRAFT"
        self.client.calls.clear()
        with self.assertRaisesRegex(ValueError, "内容与请求不符"):
            await self.client.ensure_version("harnesses", "test", spec)
        self.assertFalse(any(method == "POST" for method, _, _ in self.client.calls))

    async def test_published_version_reuse_checks_identity_not_spec_or_tags(self) -> None:
        spec = {"runtime": {"type": "runner", "uri": "fixed"}, "ttl_seconds": 600}
        ref = await self.client.ensure_version("harnesses", "test", spec)
        saved = self.client.versions[f"/harnesses/{ref['key']}/versions/1.0.0"]
        saved["spec"] = {"server_normalized": True}
        self.client.entities[f"/harnesses/{ref['key']}"]["tags"] = []
        self.client.calls.clear()
        with patch(
            "areal_rsi.infra.tasks.remote.envarena.client.matching_spec",
            side_effect=AssertionError("Published specs must not be compared"),
        ):
            self.assertEqual(await self.client.ensure_version("harnesses", "test", spec), ref)
        self.assertFalse(any(method == "POST" for method, _, _ in self.client.calls))

    async def test_reuse_rejects_wrong_key_version_and_unpublished_state(self) -> None:
        spec = {"runtime": {"type": "runner", "uri": "fixed"}}
        ref = await self.client.ensure_version("harnesses", "test", spec)
        saved = self.client.versions[f"/harnesses/{ref['key']}/versions/1.0.0"]
        for field, value in (("key", "wrong"), ("version", "2.0.0"), ("status", "DEPRECATED")):
            with self.subTest(field=field):
                original = saved[field]
                saved[field] = value
                self.client.calls.clear()
                with self.assertRaises(ValueError):
                    await self.client.ensure_version("harnesses", "test", spec)
                self.assertFalse(any(method == "POST" for method, _, _ in self.client.calls))
                saved[field] = original

    async def test_missing_aggregation_uses_validated_defaults_in_subset(self) -> None:
        self.client.versions["/benchmarks/base/versions/1.0.0"]["spec"].pop("aggregation")
        benchmark = ArenaBenchmark(self.client, self.config)
        await benchmark.load()
        ref = await benchmark.ensure_subset(await benchmark.list_tasks(BenchmarkSplit.TRAIN))
        published = await self.client.version("benchmarks", ref)
        self.assertEqual(
            published["spec"]["aggregation"],
            {"method": "mean", "failed_task_policy": "failed_as_zero"},
        )

    async def test_case_benchmark_accepts_null_env_refs_but_rejects_content_changes(self) -> None:
        spec = {"case_refs": [{"case_ref": REF, "weight": 1}], "reward_ref": REF}
        ref = await self.client.ensure_version("benchmarks", "test", spec)
        saved = self.client.versions[f"/benchmarks/{ref['key']}/versions/1.0.0"]
        saved["spec"]["env_refs"] = None
        for status in ("DRAFT", "PUBLISHED"):
            saved["status"] = status
            self.assertEqual(await self.client.ensure_version("benchmarks", "test", spec), ref)
        original = copy.deepcopy(saved["spec"])
        for field, value in (
            ("env_refs", [{"env_ref": REF}]),
            ("reward_ref", {"key": "different", "version": "1.0.0"}),
            ("unknown_field", None),
        ):
            with self.subTest(field=field):
                saved["status"] = "DRAFT"
                saved["spec"] = {**original, field: value}
                with self.assertRaisesRegex(ValueError, "内容与请求不符"):
                    await self.client.ensure_version("benchmarks", "test", spec)

    async def test_verifier_infra_failure_is_not_aggregated_as_zero_reward(self) -> None:
        result = VerificationResult(
            "task", "trace", None, None, {}, "", status=VerificationStatus.INFRA_ERROR
        )
        with self.assertRaisesRegex(ValueError, "Verifier 未成功完成"):
            await ArenaEvaluation().evaluate(self.solution, BenchmarkSplit.TRAIN, [result])

    async def test_invalid_case_weights_are_rejected_without_changing_scoring(self) -> None:
        spec = self.client.versions["/benchmarks/base/versions/1.0.0"]["spec"]
        for weight in (0, -1, None, True, "1", float("inf"), float("nan")):
            with self.subTest(weight=weight):
                spec["case_refs"][0]["weight"] = weight
                with self.assertRaisesRegex(ValueError, "weight"):
                    await ArenaBenchmark(self.client, self.config).load()

    async def test_pagination_uses_total_when_server_returns_smaller_pages(self) -> None:
        get = AsyncMock(
            side_effect=[
                {"items": [{"id": 1}], "total": 3},
                {"items": [{"id": 2}], "total": 3},
                {"items": [{"id": 3}], "total": 3},
            ]
        )
        with patch.object(self.client, "get", get):
            self.assertEqual(
                await self.client.items("/jobs", "jobs"), [{"id": 1}, {"id": 2}, {"id": 3}]
            )
        self.assertEqual(
            [parse_qs(urlsplit(call.args[0]).query)["offset"][0] for call in get.await_args_list],
            ["0", "1", "2"],
        )

    async def test_invalid_pagination_response_is_not_silently_empty(self) -> None:
        invalid_pages: tuple[Any, ...] = (
            {"unknown": []},
            None,
            {"items": ["invalid"]},
            {"items": [], "total": 1},
            {"items": [], "total": -1},
        )
        for page in invalid_pages:
            with (
                self.subTest(page=page),
                patch.object(self.client, "get", AsyncMock(return_value=page)),
                self.assertRaisesRegex(ValueError, "分页"),
            ):
                await self.client.items("/jobs", "jobs")

    def test_unsafe_or_unsupported_configuration_is_rejected(self) -> None:
        invalid: tuple[Any, ...] = (
            {"runtime": {}},
            {"model": "different"},
            {"mcp": {"mcpServers": {"x": {"command": "node", "env": {"API_KEY": "secret"}}}}},
            {"skills": ["x@latest"]},
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_change(value, self.config.allowed_changes)
        self.assertEqual(
            redact({"prompt_tokens": 3, "API_KEY": "x"}),
            {"prompt_tokens": 3, "API_KEY": "[redacted]"},
        )

    async def test_benchmark_rejects_harness_override_and_unsupported_aggregation(self) -> None:
        spec = self.client.versions["/benchmarks/base/versions/1.0.0"]["spec"]
        spec["case_refs"][0]["harness_ref"] = REF
        with self.assertRaisesRegex(ValueError, "override"):
            await ArenaBenchmark(self.client, self.config).load()
        del spec["case_refs"][0]["harness_ref"]
        spec["aggregation"]["failed_task_policy"] = "exclude"
        with self.assertRaisesRegex(ValueError, "评分语义"):
            await ArenaBenchmark(self.client, self.config).load()

    async def test_config_resolves_source_job_envelope_and_disjoint_splits(self) -> None:
        self.client.jobs["source"] = {
            "job_id": "source",
            "benchmark_ref": REF,
            "harness_ref": REF,
            "model_ref": REF,
            "tasks": [],
        }
        path = self.root / "config.yaml"
        path.write_text(
            "source_job: source\ntrain_cases: [train-a]\nvalidate_cases: [validate]\n"
            "allowed_changes: [system_prompt]\n"
        )
        config = await ArenaConfig.load(path, self.client)
        self.assertEqual(config.harness, REF)
        path.write_text("source_job: source\ntrain_cases: [train-a]\nvalidate_cases: [train-a]\n")
        with self.assertRaisesRegex(ValueError, "重叠"):
            await ArenaConfig.load(path, self.client)

    async def test_artifact_globs_are_loaded_and_validated(self) -> None:
        path = self.root / "artifacts.yaml"
        base = (
            "benchmark: base@1.0.0\nharness: base@1.0.0\nmodel: base@1.0.0\n"
            "train_cases: [train-a]\nvalidate_cases: [validate]\n"
        )
        for patterns in ([], ["*.js", "outputs/*.txt"]):
            with self.subTest(patterns=patterns):
                path.write_text(base + "artifact_globs: " + json.dumps(patterns))
                loaded = await ArenaConfig.load(path, self.client)
                self.assertEqual(loaded.artifact_globs, tuple(patterns))
        for invalid in ("*.js", [None], [""], ["../*.js"], ["/absolute/*"], ["dir\\*.js"]):
            with self.subTest(invalid=invalid):
                path.write_text(base + "artifact_globs: " + json.dumps(invalid))
                with self.assertRaisesRegex(ValueError, "artifact_globs"):
                    await ArenaConfig.load(path, self.client)
