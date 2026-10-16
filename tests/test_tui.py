"""Real PTY → upstream DSH views → Python transport → native Codex, offline."""

from __future__ import annotations

import errno
import json
import os
import pty
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Callable
from pathlib import Path

from native_model import Json, NativeModel, function

from areal_rsi.api.control import decode_record
from areal_rsi.api.research import SessionRecord


@unittest.skipUnless(shutil.which("codex") and shutil.which("node"), "Codex and Node are required")
class TUITests(unittest.TestCase):
    def test_native_chat_rsi_context_and_reopen_in_a_real_terminal(self) -> None:
        def respond(request: Json) -> str | list[Json]:
            if len(model.requests) == 2:
                return function(
                    "rsi_set_task",
                    {
                        "objective": "Use business context",
                        "instructions": "Retain the discussion",
                        "acceptance": ["Earlier context is available"],
                    },
                )
            if len(model.requests) == 5:
                return function(
                    "rsi_update_goal",
                    {
                        "status": "completed",
                        "summary": "Context checked",
                        "evidence": ["BUSINESS_UI_CONTEXT_501 retained after resume"],
                    },
                )
            if len(model.requests) == 7:
                return json.dumps(
                    {
                        "slug": "business-context-handoffs",
                        "title": "Preserve business context",
                        "description": "When changing execution roles",
                        "body": "Retain business constraints and verify them after handoff.",
                    }
                )
            return "NATIVE_UI_REPLY: business context retained"

        model = NativeModel(respond)
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / ".env").write_text(
                    f"RSI_BASE_URL={model.url}\nRSI_API_KEY=offline-fixture\n"
                    "RSI_AUTO_LEARN=0\nRSI_DATA_ROOT=./data\nRSI_MODEL=gpt-6-astra\n"
                )
                environment = {
                    key: value for key, value in os.environ.items() if not key.startswith("RSI_")
                }
                environment["TERM"] = "xterm-256color"

                def session(resume: str = "") -> str:
                    master, slave = pty.openpty()
                    arguments = [sys.executable, "-m", "areal_rsi", "tui"]
                    if resume:
                        arguments.extend(["--resume", resume])
                    process = subprocess.Popen(
                        arguments,
                        stdin=slave,
                        stdout=slave,
                        stderr=slave,
                        cwd=root,
                        env=environment,
                        start_new_session=True,
                    )
                    os.close(slave)
                    output = bytearray()

                    def until(predicate: Callable[[], bool]) -> None:
                        deadline = time.monotonic() + 25
                        while not predicate():
                            if time.monotonic() > deadline:
                                self.fail(output.decode(errors="replace")[-5000:])
                            if process.poll() is not None:
                                if predicate():
                                    return
                                self.fail(output.decode(errors="replace")[-5000:])
                            if select.select([master], [], [], 0.05)[0]:
                                try:
                                    data = os.read(master, 65536)
                                except OSError as exc:
                                    if exc.errno != errno.EIO:
                                        raise
                                    data = b""  # Linux reports PTY closure as EIO, not empty bytes.
                                if not data:
                                    process.wait(timeout=max(0.1, deadline - time.monotonic()))
                                    if not predicate():
                                        self.fail(output.decode(errors="replace")[-5000:])
                                    return
                                output.extend(data)

                    def state() -> dict[str, object]:
                        paths = list((root / "data/sessions").glob("*/session.json"))
                        return json.loads(paths[0].read_text()) if paths else {}

                    try:
                        until(lambda: b"DSH terminal" in output and bool(state().get("thread_id")))
                        run_id = str(state()["run_id"])
                        if not resume:
                            os.write(master, b"/learn\r")
                            until(lambda: "总结经验" in output.decode(errors="replace"))
                            os.write(master, b"BUSINESS_UI_CONTEXT_501\r")
                            until(lambda: b"NATIVE_UI_REPLY" in output)
                            until(lambda: state().get("status") == "idle")
                            os.write(master, b"/model\r")
                            until(lambda: "当前模型：" in output.decode(errors="replace"))
                            until(lambda: b"gpt-6-luna" in output)
                            self.assertEqual(
                                model.catalog_requests, [("/v1/models", "Bearer offline-fixture")]
                            )
                            self.assertEqual(len(model.requests), 1)
                            os.write(master, b"/model gpt-6-sol\r")
                            until(lambda: "从下一回合生效" in output.decode(errors="replace"))
                            os.write(master, b"/effort\r")
                            until(lambda: "用法：/effort" in output.decode(errors="replace"))
                            os.write(master, b"/effort low\r")
                            until(lambda: "effort：low" in output.decode(errors="replace"))
                            os.write(master, b"/learn-mode auto\r")
                            until(lambda: "经验总结模式：auto" in output.decode(errors="replace"))
                            os.write(master, b"/learn-mode guided\r")
                            until(lambda: "经验总结模式：guided" in output.decode(errors="replace"))
                            os.write(master, b"/rsi use earlier business discussion\r")
                            until(
                                lambda: len(model.requests) == 3 and state().get("status") == "idle"
                            )
                            self.assertIn("BUSINESS_UI_CONTEXT_501", json.dumps(model.requests[1]))
                            self.assertEqual(model.requests[1]["model"], "gpt-6-sol")
                            self.assertEqual(model.requests[1]["reasoning"]["effort"], "low")
                            self.assertEqual(state()["phase"], "interaction")
                            os.write(master, b"/budget\r")
                            until(lambda: "Token：0 / 不限" in output.decode(errors="replace"))
                            os.write(master, b"/budget 1 off\r")
                            until(lambda: "Token：0 / 1" in output.decode(errors="replace"))
                            os.write(master, b"/start\r")
                            until(lambda: state().get("status") == "budget_limited")
                            until(lambda: not state().get("active_turn_id"))
                            until(lambda: "预算已耗尽" in output.decode(errors="replace"))
                        else:
                            until(lambda: b"NATIVE_UI_REPLY" in output)
                            until(lambda: b"gpt-6-sol" in output)
                            self.assertEqual(state()["phase"], "leader")
                            self.assertEqual(state()["status"], "budget_limited")
                            self.assertEqual(len(model.requests), 4)
                            os.write(master, b"/effort\r")
                            until(lambda: "effort：low" in output.decode(errors="replace"))
                            os.write(master, b"/learn-mode\r")
                            until(lambda: "经验总结模式：guided" in output.decode(errors="replace"))
                            before = state()
                            os.write(master, b"/budget off\r")
                            until(lambda: state().get("status") == "paused")
                            self.assertEqual(state()["tokens_used"], before["tokens_used"])
                            self.assertEqual(len(model.requests), 4)
                            os.write(master, b"/resume\r")
                            until(lambda: state().get("status") == "completed")
                            self.assertEqual(state()["thread_id"], before["thread_id"])
                            self.assertIn("BUSINESS_UI_CONTEXT_501", json.dumps(model.requests[4]))
                            self.assertEqual(len(model.requests), 6)
                            until(lambda: "等待经验复盘" in output.decode(errors="replace"))
                            self.assertEqual(
                                decode_record(SessionRecord, state()).learning.status,
                                "awaiting_feedback",
                            )
                            os.write(master, b"/learn Preserve context before visual polish\r")
                            until(
                                lambda: (
                                    decode_record(SessionRecord, state()).learning.status
                                    == "completed"
                                )
                            )
                            self.assertIn(
                                "Preserve context before visual polish",
                                json.dumps(model.requests[6]),
                            )
                            document = decode_record(SessionRecord, state()).learning.document
                            assert document is not None
                            self.assertEqual(
                                Path(document.path).name, "business-context-handoffs.md"
                            )
                            self.assertTrue(Path(document.path).is_file())
                            readable = root / "data/experience/business-context-handoffs.md"
                            self.assertEqual(readable.resolve(), Path(document.path))
                            until(lambda: "经验已沉淀" in output.decode(errors="replace"))
                            self.assertNotIn(document.revision.encode(), output)
                            os.write(master, b"/docs\r")
                            until(lambda: b"When changing execution roles" in output)
                            self.assertNotIn(document.revision.encode(), output)
                            self.assertEqual(state()["thread_id"], before["thread_id"])
                        os.write(master, b"\x11")  # Ctrl+Q: graceful shutdown and terminal restore.
                        until(lambda: process.poll() is not None)
                        self.assertEqual(process.returncode, 0, output.decode(errors="replace"))
                        return run_id
                    finally:
                        if process.poll() is None:
                            os.killpg(process.pid, signal.SIGTERM)
                            process.wait(timeout=15)
                        os.close(master)

                run_id = session()
                session(run_id)
                self.assertFalse(model.errors)
        finally:
            model.close()
