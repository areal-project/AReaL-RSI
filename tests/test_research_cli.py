"""CLI lifecycle checks use an application fake and never invoke a model service."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import io
import json
import os
import signal
import tempfile
import unittest
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from areal_rsi.ui.cli import main, run_research, serve_workbench

Json = dict[str, Any]


def progress(status: str = "running", *, actor_state: str = "running") -> Json:
    return {
        "run_id": "research_example",
        "status": status,
        "simulated": True,
        "nodes": [{"iteration_id": "node_1", "state": "running"}],
        "agents": [{"agent_id": "rsi", "state": actor_state}],
        "jobs": [],
        "best": None,
        "budget": {"model_calls": 1, "total_tokens": 10, "verify_calls": 0},
        "event_cursor": 1,
        "worker_alive": True,
        "waiting_for_user": [],
    }


class FakeApp:
    def __init__(self, frames: list[Json]):
        self.frames = frames
        self.position = 0
        self.calls: list[tuple[str, Json]] = []
        self.closed = False
        self.on_detail: Callable[[], None] | None = None

    async def call(self, action: str, params: Json) -> Json:
        self.calls.append((action, params))
        if action == "create":
            return progress("planned", actor_state="waiting")
        if action in {"start", "resume"}:
            return copy.deepcopy(self.frames[0])
        if action == "detail":
            if self.on_detail:
                self.on_detail()
            self.position = min(self.position + 1, len(self.frames) - 1)
            return copy.deepcopy(self.frames[self.position])
        raise AssertionError("Unexpected application mutation: " + action)

    async def close(self) -> None:
        self.closed = True


class ResearchCliTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.environment = patch.dict(
            os.environ,
            {
                "RSI_DATA_ROOT": str(self.root),
                "RSI_API_KEY": "fake-test-key",
                "RSI_BASE_URL": "https://example.test",
                "RSI_MODEL": "gpt-6-astra",
                "RSI_REASONING_EFFORT": "xhigh",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    async def test_example_starts_real_runtime_configuration_and_reports_success(self) -> None:
        last = progress("succeeded", actor_state="waiting")
        last["best"] = {"candidate_id": "best", "score": 0}
        app = FakeApp([progress(), last])
        output = io.StringIO()
        with (
            patch("areal_rsi.research_app.ResearchApp", return_value=app) as factory,
            contextlib.redirect_stdout(output),
        ):
            code = await run_research(example="kernelbench", simulate_gpu=True, poll_interval=0.001)
        self.assertEqual(code, 0)
        self.assertTrue(app.closed)
        self.assertEqual([action for action, _ in app.calls][:2], ["create", "start"])
        self.assertTrue(factory.call_args.kwargs["allow_simulation"])
        config = factory.call_args.kwargs["config"]
        self.assertEqual(config.runtime().model, "gpt-6-astra")
        self.assertEqual(config.runtime().reasoning_effort, "xhigh")
        self.assertEqual(config.metadata["template"], "kernelbench")
        self.assertNotIn("fake-test-key", output.getvalue())
        result = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual(result["best_score"], 0)
        self.assertTrue(result["simulated"])

    async def test_waiting_for_user_is_not_success_and_outputs_web_resume_command(self) -> None:
        waiting = progress(actor_state="waiting")
        waiting["waiting_for_user"] = ["Which GPU should be used?"]
        app = FakeApp([waiting])
        output = io.StringIO()
        with (
            patch("areal_rsi.research_app.ResearchApp", return_value=app),
            contextlib.redirect_stdout(output),
        ):
            code = await run_research(example="cuhkx", simulate_gpu=True, poll_interval=0.001)
        self.assertEqual(code, 3)
        result = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual(result["status"], "waiting_for_user")
        self.assertEqual(result["saved_status"], "running")
        self.assertIn("rsi workbench --simulate-gpu", result["workbench_command"])
        self.assertNotIn("stop", [action for action, _ in app.calls])
        self.assertTrue(app.closed)

    async def test_pending_message_prevents_premature_idle_exit(self) -> None:
        ready = progress(actor_state="waiting")
        ready["pending_agents"] = ["rsi"]
        app = FakeApp([ready, ready, progress("succeeded", actor_state="waiting")])
        with (
            patch("areal_rsi.research_app.ResearchApp", return_value=app),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            code = await run_research(example="cuhkx", simulate_gpu=True, poll_interval=0.001)
        self.assertEqual(code, 0)
        self.assertEqual(app.position, 2)

    async def test_dead_worker_is_reported_without_waiting_for_saved_running_actor(self) -> None:
        interrupted = progress(actor_state="running")
        interrupted["worker_alive"] = False
        app = FakeApp([interrupted])
        output = io.StringIO()
        with (
            patch("areal_rsi.research_app.ResearchApp", return_value=app),
            contextlib.redirect_stdout(output),
        ):
            code = await run_research(resume="research_example", poll_interval=0.001)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue().splitlines()[-1])["status"], "worker_stopped")
        self.assertNotIn("stop", [action for action, _ in app.calls])

    async def test_timeout_closes_worker_and_retains_existing_run_for_resume(self) -> None:
        app = FakeApp([progress()])
        output = io.StringIO()
        with (
            patch("areal_rsi.research_app.ResearchApp", return_value=app),
            contextlib.redirect_stdout(output),
        ):
            code = await run_research(
                example="kernelbench", simulate_gpu=True, timeout=0.01, poll_interval=0.001
            )
        self.assertEqual(code, 124)
        self.assertTrue(app.closed)
        self.assertNotIn("stop", [action for action, _ in app.calls])
        result = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual(result["saved_status"], "running")
        self.assertIn("--resume research_example", result["resume_command"])

    async def test_sigterm_requests_persistent_shutdown_instead_of_stopping_run(self) -> None:
        app = FakeApp([progress()])
        handlers: dict[signal.Signals, Callable[[], None]] = {}

        def register(sig: signal.Signals, callback: Callable[..., None], *args: Any) -> None:
            handlers[sig] = lambda: callback(*args)

        app.on_detail = lambda: handlers[signal.SIGTERM]()
        loop = asyncio.get_running_loop()
        with (
            patch("areal_rsi.research_app.ResearchApp", return_value=app),
            patch.object(loop, "add_signal_handler", side_effect=register),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            code = await run_research(example="kernelbench", simulate_gpu=True, poll_interval=0.001)
        self.assertEqual(code, 143)
        self.assertTrue(app.closed)
        self.assertNotIn("stop", [action for action, _ in app.calls])

    async def test_resume_uses_saved_run_and_does_not_create_or_start_another(self) -> None:
        app = FakeApp([progress("succeeded", actor_state="waiting")])
        with (
            patch("areal_rsi.research_app.ResearchApp", return_value=app),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            code = await run_research(resume="research_example", simulate_gpu=True)
        self.assertEqual(code, 0)
        self.assertEqual(app.calls, [("resume", {"run_id": "research_example"})])
        self.assertTrue(app.closed)

    async def test_bad_arguments_and_missing_key_fail_before_creating_run(self) -> None:
        with patch("areal_rsi.research_app.ResearchApp") as factory:
            with self.assertRaisesRegex(ValueError, "--simulate-gpu"):
                await run_research(example="kernelbench")
            with self.assertRaisesRegex(ValueError, "exactly one"):
                await run_research(example="kernelbench", resume="existing", simulate_gpu=True)
            with (
                patch.dict(os.environ, {"RSI_API_KEY": ""}),
                self.assertRaisesRegex(ValueError, "RSI_API_KEY"),
            ):
                await run_research(example="kernelbench", simulate_gpu=True)
            factory.assert_not_called()


class ResearchParserTest(unittest.TestCase):
    def test_research_arguments_route_to_new_entry(self) -> None:
        with (
            patch(
                "sys.argv",
                ["rsi", "research", "--example", "cuhkx", "--simulate-gpu", "--timeout", "42"],
            ),
            patch("areal_rsi.ui.cli.run_research", new_callable=AsyncMock, return_value=3) as run,
            self.assertRaises(SystemExit) as caught,
        ):
            main()
        self.assertEqual(caught.exception.code, 3)
        self.assertEqual(run.call_args.kwargs["example"], "cuhkx")
        self.assertTrue(run.call_args.kwargs["simulate_gpu"])
        self.assertEqual(run.call_args.kwargs["timeout"], 42)

    def test_workbench_arguments_preserve_existing_readonly_web_command(self) -> None:
        with (
            patch("sys.argv", ["rsi", "workbench", "--port", "8989", "--simulate-gpu"]),
            patch("areal_rsi.ui.cli.serve_workbench", return_value=0) as serve,
            self.assertRaises(SystemExit) as caught,
        ):
            main()
        self.assertEqual(caught.exception.code, 0)
        serve.assert_called_once_with(port=8989, config_file=None, simulate_gpu=True)
        with (
            patch("sys.argv", ["rsi", "web", "--port", "8765"]),
            patch("areal_rsi.ui.web.serve") as readonly,
        ):
            main()
        readonly.assert_called_once()

    def test_workbench_closes_app_on_background_loop_before_server(self) -> None:
        app = FakeApp([])
        closed: list[str] = []

        class Server:
            url = "http://127.0.0.1:8766"
            loop = object()

            def serve_forever(self) -> None:
                return None

            def server_close(self) -> None:
                self.assert_closed()
                closed.append("server")

            def assert_closed(self) -> None:
                if not app.closed:
                    raise AssertionError("Application must close before its event loop")

        class Future:
            def result(self, timeout: float) -> None:
                return None

        def submit(coroutine: Coroutine[Any, Any, None], loop: object) -> Future:
            asyncio.run(coroutine)
            return Future()

        with (
            patch("areal_rsi.research_app.ResearchApp", return_value=app),
            patch("areal_rsi.ui.workbench.WorkbenchServer", return_value=Server()),
            patch("asyncio.run_coroutine_threadsafe", side_effect=submit),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(serve_workbench(port=8766, config_file=None, simulate_gpu=True), 0)
        self.assertEqual(closed, ["server"])


if __name__ == "__main__":
    unittest.main()
