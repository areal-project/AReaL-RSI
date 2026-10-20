"""Real app-server against an offline endpoint: no external API or account is used."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import struct
import sys
import tempfile
import threading
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from native_model import Json, NativeModel, function

from areal_rsi.api.research import SessionConfig, SessionPhase, SessionStatus
from areal_rsi.infra.config import load_config
from areal_rsi.infra.session import SessionSystem


async def idle(system: SessionSystem, run_id: str) -> None:
    async with asyncio.timeout(20):
        while True:
            record = system.detail(run_id)
            if record.status == SessionStatus.FAILED:
                raise AssertionError(record.last_error)
            if (
                not record.pending_inputs
                and not record.active_turn_id
                and record.status
                in {
                    SessionStatus.IDLE,
                    SessionStatus.COMPLETED,
                    SessionStatus.WAITING,
                    SessionStatus.BUDGET_LIMITED,
                }
            ):
                return
            await asyncio.sleep(0.05)


class ProviderCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_uses_configured_api_and_reports_invalid_responses(self) -> None:
        model = NativeModel(lambda request: "Unexpected model call")
        try:
            with (
                tempfile.TemporaryDirectory() as directory,
                patch.dict(os.environ, {"RSI_TEST_KEY": "catalog-fixture"}),
            ):
                root = Path(directory)
                system = SessionSystem(root / "data")
                run_id = await system.create(
                    SessionConfig(str(root), base_url=model.url, api_key_env="RSI_TEST_KEY")
                )
                self.assertEqual(
                    await system.list_models(run_id), ["gpt-6-astra", "gpt-6-luna", "gpt-6-sol"]
                )
                self.assertEqual(model.catalog_requests, [("/v1/models", "Bearer catalog-fixture")])
                self.assertFalse(system.detail(run_id).thread_id)
                model.catalog_status = 401
                with self.assertRaisesRegex(ValueError, "HTTP 401"):
                    await system.list_models(run_id)
                model.catalog_status = 200
                model.catalog = {"data": [{"id": 123}]}
                with self.assertRaises(ValueError):
                    await system.list_models(run_id)
                self.assertFalse(model.requests)
        finally:
            model.close()


@unittest.skipUnless(
    shutil.which("codex"), "Codex >= 0.162.1 is required for native protocol tests"
)
class NativeContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_permissions_allow_work_outside_workspace_without_approval(self) -> None:
        # Use the home directory: native workspace sandboxes may already permit /tmp writes.
        with (
            tempfile.TemporaryDirectory(
                prefix=".rsi-permissions-test-", dir=Path.home()
            ) as directory,
            patch.dict(os.environ, {"RSI_TEST_KEY": "fixture"}),
        ):
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            target = root / "outside-workspace.txt"

            def write(label: str) -> list[Json]:
                return function(
                    "exec_command",
                    {"cmd": f"printf '%s\\n' {shlex.quote(label)} >> {shlex.quote(str(target))}"},
                )

            # Scripted calls deliberately test permissions independently of role instructions.
            steps: list[str | list[Json]] = [
                write("ordinary"),
                "Ordinary work finished",
                function(
                    "rsi_set_task",
                    {
                        "objective": "Append a marker",
                        "instructions": "Use the given path",
                        "acceptance": ["The marker is written"],
                    },
                ),
                function("rsi_start_goal", {}),
                "Handing off",
                write("leader"),
                function(
                    "rsi_update_goal",
                    {"status": "completed", "summary": "Done", "evidence": ["Marker appended"]},
                ),
                "Goal completed",
                write("curator"),
                json.dumps(
                    {
                        "slug": "check-results",
                        "title": "Check results",
                        "description": "After a change",
                        "body": "Inspect the output.",
                    }
                ),
            ]
            model = NativeModel(lambda request: steps.pop(0) if steps else "Unexpected call")
            system = SessionSystem(root / "data")
            try:
                for name in ("RSI_SANDBOX", "RSI_APPROVAL_POLICY"):
                    os.environ.pop(name, None)
                os.environ.update(
                    RSI_BASE_URL=model.url, RSI_API_KEY_ENV="RSI_TEST_KEY", RSI_AUTO_LEARN="0"
                )
                env_file = root / "empty.env"
                env_file.write_text("")
                run_id = await system.create(load_config(workspace, env_file=env_file).session)
                await system.send_message(run_id, f"Append to {target}")
                await idle(system, run_id)
                await system.enter_rsi(run_id, "Append another marker and finish")
                async with asyncio.timeout(30):
                    while system.detail(run_id).status != SessionStatus.COMPLETED:
                        self.assertNotEqual(system.detail(run_id).status, SessionStatus.FAILED)
                        await asyncio.sleep(0.05)
                await system.learn(run_id)
                self.assertEqual(target.read_text(), "ordinary\nleader\ncurator\n")
                self.assertFalse(system.prompts(run_id))
                self.assertFalse(model.errors)
                self.assertFalse(steps)
            finally:
                await system.close()
                model.close()

    async def test_curator_preserves_explicit_read_only_and_excludes_later_source_turns(
        self,
    ) -> None:
        calls = 0

        def response(request: Json) -> str | list[Json]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return "OLD_COMPLETED_TASK_301"
            if calls == 2:
                return "LATER_PRIVATE_DISCUSSION_302"
            if calls == 3:
                return function("exec_command", {"cmd": "printf changed > protected.txt"})
            return json.dumps(
                {
                    "slug": "verify-changes",
                    "title": "Verify changes",
                    "description": "After edits",
                    "body": "Check the observable result before reporting completion.",
                }
            )

        model = NativeModel(response)
        try:
            with (
                tempfile.TemporaryDirectory() as directory,
                patch.dict(os.environ, {"RSI_TEST_KEY": "fixture"}),
            ):
                root = Path(directory)
                target = root / "protected.txt"
                target.write_text("original")
                system = SessionSystem(root / "data")
                try:
                    run_id = await system.create(
                        SessionConfig(
                            str(root),
                            base_url=model.url,
                            api_key_env="RSI_TEST_KEY",
                            auto_learn=False,
                            sandbox="read-only",
                        )
                    )
                    await system.send_message(run_id, "Complete the first task")
                    await idle(system, run_id)
                    snapshot = system.detail(run_id)
                    await system.send_message(run_id, "Start a later discussion")
                    await idle(system, run_id)
                    await system.experience.consolidate(snapshot, system.root)
                    self.assertIn("OLD_COMPLETED_TASK_301", json.dumps(model.requests[2]))
                    self.assertNotIn("LATER_PRIVATE_DISCUSSION_302", json.dumps(model.requests[2]))
                    self.assertEqual(target.read_text(), "original")
                    self.assertFalse(model.errors)
                finally:
                    await system.close()
        finally:
            model.close()

    async def test_experience_curation_and_retrieval_in_a_fresh_conversation(self) -> None:
        calls = 0
        document_path = ""

        def response(request: Json) -> str | list[Json]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return "SOURCE_ONLY_CONTEXT_210: mutation left a stale cache"
            if calls == 2:
                return json.dumps(
                    {
                        "slug": "transactional-cache-validation",
                        "title": "Transactional cache validation",
                        "description": "Use when changing cached dependency structures",
                        "body": (
                            "GENERAL_METHOD_211: validate invariants before commit; "
                            "compare cached results with an independent oracle."
                        ),
                    }
                )
            if calls == 3:
                return function("exec_command", {"cmd": "cat " + shlex.quote(document_path)})
            return "I applied the reusable method in this fresh conversation."

        model = NativeModel(response)
        try:
            with (
                tempfile.TemporaryDirectory() as directory,
                patch.dict(os.environ, {"RSI_TEST_KEY": "fixture"}),
            ):
                root = Path(directory)
                system = SessionSystem(root / "data")
                try:
                    config = SessionConfig(
                        str(root), base_url=model.url, api_key_env="RSI_TEST_KEY", auto_learn=False
                    )
                    source = await system.create(config)
                    await system.send_message(source, "Review a cached update")
                    await idle(system, source)
                    source_thread = system.detail(source).thread_id
                    await system.set_model(source, "gpt-6-sol")
                    await system.set_effort(source, "medium")
                    doc = await system.learn(
                        source, "An independent check revealed stale cached data"
                    )
                    document_path = doc.path
                    self.assertEqual(system.detail(source).thread_id, source_thread)
                    self.assertEqual(model.requests[0]["model"], config.model)
                    self.assertEqual(model.requests[1]["model"], "gpt-6-sol")
                    self.assertEqual(model.requests[1]["reasoning"]["effort"], "medium")
                    self.assertIn("SOURCE_ONLY_CONTEXT_210", json.dumps(model.requests[1]))
                    self.assertIn(
                        "经验文档整理者", json.dumps(model.requests[1], ensure_ascii=False)
                    )
                    self.assertEqual(system.detail(source).learning.status, "completed")
                    next_run = await system.create(config)
                    await system.send_message(next_run, "Improve a cached dependency structure")
                    await idle(system, next_run)
                    self.assertNotEqual(source_thread, system.detail(next_run).thread_id)
                    self.assertNotIn("SOURCE_ONLY_CONTEXT_210", json.dumps(model.requests[2]))
                    self.assertIn("GENERAL_METHOD_211", json.dumps(model.requests[3]))
                    self.assertFalse(model.errors)
                finally:
                    await system.close()
        finally:
            model.close()

    async def test_cli_loads_project_env_and_isolates_native_home(self) -> None:
        model = NativeModel(lambda request: "CLI context retained")
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                normal_home = root / "normal-codex"
                normal_home.mkdir()
                marker = normal_home / "config.toml"
                marker.write_text("This is deliberately not valid TOML")
                (root / ".env").write_text(
                    f"RSI_BASE_URL={model.url}\nRSI_API_KEY=offline-fixture\n"
                    "RSI_MODEL=gpt-6-astra\nRSI_AUTO_LEARN=0\nRSI_DATA_ROOT=./rsi-data\n"
                )
                environment = {
                    key: value for key, value in os.environ.items() if not key.startswith("RSI_")
                }
                environment["CODEX_HOME"] = str(normal_home)

                async def invoke(*arguments: str) -> list[Json]:
                    process = await asyncio.create_subprocess_exec(
                        sys.executable,
                        "-c",
                        "from areal_rsi.ui.cli import main; main()",
                        "run",
                        *arguments,
                        cwd=root,
                        env=environment,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    try:
                        stdout, stderr = await asyncio.wait_for(process.communicate(), 20)
                    except TimeoutError:
                        process.kill()
                        await process.wait()
                        raise
                    self.assertEqual(process.returncode, 0, stderr.decode())
                    return [json.loads(line) for line in stdout.splitlines()]

                initial = await invoke("CLI_BUSINESS_CONTEXT_91")
                run_id = initial[0]["run_id"]
                await invoke("--resume", run_id)
                self.assertEqual(len(model.requests), 1, "Idle resume should not call the model")
                await invoke("--resume", run_id, "Continue the same discussion")
                self.assertIn("CLI_BUSINESS_CONTEXT_91", json.dumps(model.requests[-1]))
                self.assertEqual(marker.read_text(), "This is deliberately not valid TOML")
                self.assertEqual(list(normal_home.iterdir()), [marker])
                self.assertTrue(list((root / "rsi-data/codex/sessions").rglob("*.jsonl")))
                session = root / "rsi-data/sessions" / run_id / "session.json"
                self.assertNotIn("offline-fixture", session.read_text())
        finally:
            model.close()

    async def test_rsi_forks_native_history_and_continues_goal(self) -> None:
        steps: list[str | list[Json]] = [
            function("exec_command", {"cmd": "printf 'ORIGINAL_TOOL_RESULT_83'"}),
            "ORDINARY_REPLY_82",
            function(
                "rsi_set_task",
                {
                    "objective": "Improve the shipment page",
                    "instructions": (
                        "Retain parent_order_id business semantics and the visual reference."
                    ),
                    "acceptance": ["The page has been checked"],
                },
            ),
            function("rsi_start_goal", {}),
            "INTERACTION_HANDOFF_84",
            function("exec_command", {"cmd": "printf 'done' > result.txt"}),
            "Work checkpoint; final validation remains.",
            function(
                "rsi_update_goal",
                {
                    "status": "completed",
                    "summary": "Finished the requested page",
                    "evidence": ["result.txt exists after the actual tool execution"],
                },
            ),
            "GOAL_COMPLETE_85",
        ]
        model = NativeModel(lambda request: steps.pop(0) if steps else "Unexpected extra turn")
        try:
            with (
                tempfile.TemporaryDirectory() as directory,
                patch.dict(os.environ, {"RSI_TEST_KEY": "offline-fixture"}),
            ):
                root = Path(directory)
                config = SessionConfig(
                    str(root),
                    auto_learn=False,
                    base_url=model.url,
                    api_key_env="RSI_TEST_KEY",
                    approval_policy="never",
                )
                system = SessionSystem(root / "data")
                try:
                    run_id = await system.create(config)
                    reference = root / "reference.png"

                    def chunk(name: bytes, payload: bytes) -> bytes:
                        return (
                            struct.pack(">I", len(payload))
                            + name
                            + payload
                            + struct.pack(">I", zlib.crc32(name + payload))
                        )

                    reference.write_bytes(
                        b"\x89PNG\r\n\x1a\n"
                        + chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0))
                        + chunk(b"IDAT", zlib.compress((b"\x00" + b"\x20\x60\xff" * 16) * 16))
                        + chunk(b"IEND", b"")
                    )
                    await system.send_message(
                        run_id, "BUSINESS_CONTEXT_81: parent_order_id", images=[reference]
                    )
                    await idle(system, run_id)
                    source = system.detail(run_id).thread_id
                    await system.set_model(run_id, "gpt-6-sol")
                    await system.set_effort(run_id, "low")
                    await system.enter_rsi(run_id, "Use this context, prepare the goal and start")
                    async with asyncio.timeout(30):
                        while system.detail(run_id).status != SessionStatus.COMPLETED:
                            record = system.detail(run_id)
                            if record.status == SessionStatus.FAILED:
                                self.fail(record.last_error)
                            await asyncio.sleep(0.05)
                    record = system.detail(run_id)
                    self.assertEqual(record.phase, SessionPhase.LEADER)
                    self.assertEqual(record.source_thread_id, source)
                    self.assertNotEqual(record.thread_id, source)
                    self.assertEqual((root / "result.txt").read_text(), "done")
                    self.assertEqual(model.requests[0]["model"], config.model)
                    self.assertTrue(
                        all(request["model"] == "gpt-6-sol" for request in model.requests[2:])
                    )
                    self.assertTrue(
                        all(
                            request["reasoning"]["effort"] == "low"
                            for request in model.requests[2:]
                        )
                    )
                    for request in (model.requests[2], model.requests[5], model.requests[7]):
                        history = json.dumps(request)
                        for marker in (
                            "BUSINESS_CONTEXT_81",
                            "ORDINARY_REPLY_82",
                            "ORIGINAL_TOOL_RESULT_83",
                        ):
                            self.assertIn(marker, history)
                        self.assertTrue("data:image/" in history, "Native image payload was lost")
                    for index, role in (
                        (0, "普通工作模式"),
                        (2, "Interaction"),
                        (5, "执行 Leader"),
                    ):
                        role_messages = [
                            part["text"]
                            for item in model.requests[index]["input"]
                            if item.get("role") == "developer"
                            for part in item.get("content", [])
                            if "<rsi-role>" in part.get("text", "")
                        ]
                        self.assertTrue(role_messages, "Current role did not reach the model")
                        self.assertIn(role, role_messages[-1])
                    self.assertIn("INTERACTION_HANDOFF_84", json.dumps(model.requests[5]))
                    self.assertTrue(record.progress.evidence)
                    self.assertFalse(model.errors)
                    self.assertFalse(steps)
                    self.assertGreater(record.tokens_used, 0)
                    # Returning to chat and entering RSI again keeps the latest Leader context.
                    leader = record.thread_id
                    await system.chat(run_id)
                    await asyncio.sleep(0.3)
                    await system.send_message(run_id, "Discuss the next change")
                    await idle(system, run_id)
                    self.assertEqual(system.detail(run_id).thread_id, leader)
                    self.assertIn("GOAL_COMPLETE_85", json.dumps(model.requests[-1]))
                    current_roles = [
                        part["text"]
                        for item in model.requests[-1]["input"]
                        if item.get("role") == "developer"
                        for part in item.get("content", [])
                        if "<rsi-role>" in part.get("text", "")
                    ]
                    self.assertIn("普通工作模式", current_roles[-1])
                finally:
                    await system.close()
        finally:
            model.close()

    async def test_interrupt_and_resume_does_not_reexecute_finished_tool(self) -> None:
        waiting = threading.Event()
        release = threading.Event()
        calls = 0

        def response(request: Json) -> str | list[Json]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return function("exec_command", {"cmd": "printf 'once\\n' >> effect.txt"})
            if calls == 2:
                waiting.set()
                release.wait(15)
            return "Recovered without repeating the side effect."

        model = NativeModel(response)
        try:
            with (
                tempfile.TemporaryDirectory() as directory,
                patch.dict(os.environ, {"RSI_TEST_KEY": "offline-fixture"}),
            ):
                root = Path(directory)
                system = SessionSystem(root / "data")
                config = SessionConfig(
                    str(root),
                    auto_learn=False,
                    base_url=model.url,
                    api_key_env="RSI_TEST_KEY",
                    approval_policy="never",
                )
                try:
                    run_id = await system.create(config)
                    await system.send_message(run_id, "Apply one change, then explain it")
                    async with asyncio.timeout(15):
                        while not waiting.is_set():
                            await asyncio.sleep(0.05)
                    await system.pause(run_id)
                    async with asyncio.timeout(5):
                        while system.detail(run_id).active_turn_id:
                            await asyncio.sleep(0.05)
                    self.assertEqual(system.detail(run_id).status, SessionStatus.PAUSED)
                    release.set()
                    thread_id = system.detail(run_id).thread_id
                    await system.close()
                    system = SessionSystem(root / "data")
                    await system.open(run_id)
                    await system.resume(run_id)
                    await idle(system, run_id)
                    self.assertEqual(thread_id, system.detail(run_id).thread_id)
                    self.assertEqual((root / "effect.txt").read_text(), "once\n")
                    self.assertIn("effect.txt", json.dumps(model.requests[-1]))
                finally:
                    release.set()
                    await system.close()
        finally:
            release.set()
            model.close()

    async def test_native_conversation_resume_preserves_context(self) -> None:
        model = NativeModel(lambda request: "Remembered the business context.")
        try:
            with (
                tempfile.TemporaryDirectory() as directory,
                patch.dict(os.environ, {"RSI_TEST_KEY": "offline-fixture"}),
            ):
                root = Path(directory)
                config = SessionConfig(
                    str(root), auto_learn=False, base_url=model.url, api_key_env="RSI_TEST_KEY"
                )
                system = SessionSystem(root / "data")
                try:
                    run_id = await system.create(config)
                    await system.send_message(
                        run_id, "BUSINESS_CONTEXT_71: shipments use parent_order_id"
                    )
                    await idle(system, run_id)
                    thread_id = system.detail(run_id).thread_id
                    await system.close()
                    system = SessionSystem(root / "data")
                    await system.send_message(run_id, "How are shipments linked?")
                    await idle(system, run_id)
                    self.assertEqual(thread_id, system.detail(run_id).thread_id)
                    self.assertIn("BUSINESS_CONTEXT_71", json.dumps(model.requests[-1]))
                    self.assertFalse(model.errors)
                finally:
                    await system.close()
        finally:
            model.close()
