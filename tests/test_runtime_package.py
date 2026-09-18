from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from areal_rsi.api.base import AgentSpec, ModelVersion
from areal_rsi.base.storage import FileContentStore, FileTrajectoryStore, now
from areal_rsi.modules.runtime_package import PackageAgentRuntime, RuntimeSettings, sandbox_command


class PackageRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def _check_composition(
        self, selection: dict[str, str], *, agent_directories: bool = False
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            content = FileContentStore(root / "content")
            trajectories = FileTrajectoryStore(root / "trajectories", content)
            runtime = PackageAgentRuntime(
                root / "sessions",
                content,
                trajectories,
                settings=RuntimeSettings({"default": selection}),
                role="task-agent",
                base_url="http://127.0.0.1:1",
                api_key="local-proxy",
                agent_directories=agent_directories,
            )
            model = ModelVersion("model-test", "test", await content.put_text("model"), now())
            session = await runtime.open(AgentSpec("task-agent", "deepseek-harness@v1", model))
            expected = (
                runtime.root / "task-agent" / "sessions" if agent_directories else runtime.root
            )
            self.assertEqual(session.directory.parent, expected)
            try:
                first = await session.send("first message")
                second = await session.send("second message")
                self.assertEqual(first, "fake runtime handled 1 content block(s)")
                self.assertEqual(second, "fake runtime handled 1 content block(s)")
                self.assertEqual(len(session.run_ids), 2)
                ids = {e["sessionId"] for e in session.events}
                self.assertEqual(len(ids), 1)
            finally:
                ref = await session.finish()
            self.assertEqual(await session.finish(), ref)
            trajectory = await trajectories.get(ref.trajectory_id)
            assert trajectory is not None
            trace = await content.read_json(trajectory.content)
            assert isinstance(trace, dict)
            turns = trace["turns"]
            assert isinstance(turns, list)
            self.assertEqual(len(turns), 4)
            self.assertTrue((session.directory / "data/state").is_dir())
            self.assertEqual(list((session.directory / "data").rglob("*.sqlite*")), [])
            self.assertEqual(trace["events"], session.events)
            self.assertEqual(sum(event["type"] == "run.completed" for event in session.events), 2)
            self.assertIsNotNone(session.process.returncode)
            with self.assertRaisesRegex(RuntimeError, "已结束"):
                await session.send("late")

    async def test_installed_composition_multiturn_and_trajectory(self) -> None:
        await self._check_composition({"kind": "fake"})

    async def test_run_owned_agent_sessions(self) -> None:
        await self._check_composition({"kind": "fake"}, agent_directories=True)

    async def test_installed_entrypoint_plugin(self) -> None:
        await self._check_composition(
            {
                "kind": "entrypoint",
                "pluginEntrypoint": "areal_agent_runtime.adapters.fake.plugin:create_plugin",
            }
        )

    @unittest.skipUnless(sys.platform == "darwin", "macOS sandbox-exec integration")
    async def test_native_zsh_heredoc_uses_private_temporary_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            content = FileContentStore(root / "content")
            runtime = PackageAgentRuntime(
                root / "sessions",
                content,
                FileTrajectoryStore(root / "traces", content),
                settings=RuntimeSettings({"default": {"kind": "fake"}}),
                role="research-agent",
                base_url="http://localhost:1",
                api_key="fixture",
            )
            model = ModelVersion("model-test", "test", await content.put_text("model"), now())
            with patch(
                "asyncio.create_subprocess_exec", new=AsyncMock(return_value=Mock())
            ) as spawn:
                session = await runtime.open(
                    AgentSpec("researcher", "areal-agent-runtime@v1", model)
                )
            environment = spawn.call_args.kwargs["env"]
            prefix = ["/usr/bin/sandbox-exec", "-f", str(session.directory / "sandbox.sb")]
            result = subprocess.run(
                [*prefix, "/bin/zsh", "-lc", "cat > probe.txt <<'END'\nheredoc works\nEND"],
                env=environment,
                cwd=session.directory / "workspace",
                capture_output=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(
                (session.directory / "workspace/probe.txt").read_text(), "heredoc works\n"
            )
            denied = subprocess.run(
                [*prefix, "/bin/zsh", "-lc", 'echo private > "$1"', "zsh", str(root / "peer")],
                env=environment,
                capture_output=True,
                timeout=10,
            )
            self.assertNotEqual(denied.returncode, 0)
            self.assertFalse((root / "peer").exists())

    async def test_sandbox_denies_sibling_experiment_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            directory, workspace = root / "session", root / "workspace"
            directory.mkdir()
            workspace.mkdir()
            hidden = root / "hidden-validation.txt"
            hidden.write_text("hidden-answer")
            prefix = sandbox_command(directory, workspace, ())[:3]
            script = "import pathlib,sys; pathlib.Path(sys.argv[1]).read_text()"
            import sys

            process = await asyncio.create_subprocess_exec(
                *prefix,
                sys.executable,
                "-c",
                script,
                str(hidden),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await process.communicate()
            self.assertNotEqual(process.returncode, 0)
            self.assertNotIn(b"hidden-answer", out)
            self.assertIn(b"PermissionError", err)
            import areal_rsi

            evaluator = Path(areal_rsi.__file__).parent / "modules/benchmark.py"
            process = await asyncio.create_subprocess_exec(
                *prefix,
                sys.executable,
                "-c",
                script,
                str(evaluator),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace,
            )
            out, err = await process.communicate()
            self.assertNotEqual(process.returncode, 0)
            self.assertIn(b"PermissionError", err)

    @unittest.skipUnless(sys.platform == "darwin", "macOS sandbox-exec integration")
    async def test_shared_dsh_links_use_canonical_paths_without_broadening_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            shared = root / "shared"
            cli = shared / "dsh-cli/node_modules/.bin/dsh"
            cli.parent.mkdir(parents=True)
            cli.write_text('#!/bin/sh\nexec /bin/cat "$1"\n')
            cli.chmod(0o755)
            home = shared / "runtime-dsh/fixture"
            profile = home / "profiles/agent-runtime"
            (profile / "node_modules").mkdir(parents=True)
            for name in ("package.json", "cordis.yml", "cordis.patch.yml"):
                (profile / name).write_text("{}")
            public = home / "runtime.txt"
            public.write_text("shared runtime")
            experiment = root / "experiments/example"
            experiment.mkdir(parents=True)
            (experiment / "dsh-cli").symlink_to(shared / "dsh-cli", target_is_directory=True)
            (experiment / "runtime-dsh").symlink_to(
                shared / "runtime-dsh", target_is_directory=True
            )
            hidden = experiment / "private-validation.txt"
            hidden.write_text("hidden answer")
            content = FileContentStore(experiment / "content")
            runtime = PackageAgentRuntime(
                experiment / "sessions",
                content,
                FileTrajectoryStore(experiment / "traces", content),
                settings=RuntimeSettings({}),
                role="research-agent",
                base_url="http://localhost:1",
                api_key="local-proxy",
                dsh_root=experiment,
            )
            model = ModelVersion("model-test", "test", await content.put_text("model"), now())
            with (
                patch(
                    "areal_rsi.modules.dsh.ensure_cli",
                    return_value=experiment / "dsh-cli/node_modules/.bin/dsh",
                ),
                patch(
                    "areal_rsi.modules.runtime_dsh.ensure_profile",
                    return_value=experiment / "runtime-dsh/fixture",
                ),
                patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=Mock())),
            ):
                session = await runtime.open(
                    AgentSpec("research-agent-1", "areal-agent-runtime@v1", model)
                )
            request = json.loads((session.directory / "request.json").read_text())
            self.assertEqual(request["runtime"]["config"]["dshBin"], str(cli))
            module_link = session.directory / "dsh-home/profiles/agent-runtime/node_modules"
            self.assertEqual(module_link.readlink(), profile / "node_modules")
            prefix = ["/usr/bin/sandbox-exec", "-f", str(session.directory / "sandbox.sb")]
            allowed = subprocess.run(
                [*prefix, str(cli), str(public)], capture_output=True, timeout=10
            )
            self.assertEqual(allowed.returncode, 0, allowed.stderr.decode())
            self.assertEqual(allowed.stdout, b"shared runtime")
            denied = subprocess.run(
                [*prefix, str(cli), str(hidden)], capture_output=True, timeout=10
            )
            self.assertNotEqual(denied.returncode, 0)
            self.assertNotIn(b"hidden answer", denied.stdout)
            write = subprocess.run(
                [*prefix, "/bin/sh", "-c", 'echo changed > "$1"', "sh", str(public)],
                capture_output=True,
                timeout=10,
            )
            self.assertNotEqual(write.returncode, 0)
            self.assertEqual(public.read_text(), "shared runtime")

    def test_all_bootstrap_kinds_are_selectable(self) -> None:
        for kind in (
            "fake",
            "dsh",
            "codex-cli",
            "claude-code",
            "claude-agent-sdk",
            "anthropic",
            "hermes",
            "remote",
            "entrypoint",
        ):
            value = {"kind": kind}
            if kind == "entrypoint":
                value["pluginEntrypoint"] = "example:create_plugin"
            selected = RuntimeSettings({"default": value}).select("task-agent")
            self.assertEqual(selected["kind"], kind)

    async def test_remote_wire_with_explicit_auth_reference(self) -> None:
        import os
        import socket
        import sys
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            config = root / "remote.json"
            config.write_text(
                json.dumps(
                    {
                        "dataRoot": str(root / "remote-data"),
                        "storage": {
                            "entrypoint": (
                                "areal_agent_runtime.adapters.storage.plugin:create_plugin"
                            ),
                            "config": {"stateBackend": "file"},
                        },
                        "runtime": {"kind": "fake"},
                        "wire": {
                            "host": "127.0.0.1",
                            "port": port,
                            "allowInsecure": True,
                            "authTokenEnv": "RSI_WIRE_TEST_TOKEN",
                        },
                    }
                )
            )
            with patch.dict(os.environ, {"RSI_WIRE_TEST_TOKEN": "local-wire-fixture"}):
                with (root / "server.log").open("wb") as log:
                    server = await asyncio.create_subprocess_exec(
                        sys.executable,
                        "-I",
                        str(Path(sys.executable).with_name("areal-agent-runtime")),
                        "serve-runtime",
                        "--config",
                        str(config),
                        stdout=log,
                        stderr=log,
                    )
                try:
                    async with asyncio.timeout(15):
                        while True:
                            if server.returncode is not None:
                                self.fail((root / "server.log").read_text())
                            try:
                                _reader, writer = await asyncio.open_connection("127.0.0.1", port)
                                writer.close()
                                await writer.wait_closed()
                                break
                            except OSError:
                                await asyncio.sleep(0.05)
                    content = FileContentStore(root / "content")
                    runtime = PackageAgentRuntime(
                        root / "sessions",
                        content,
                        FileTrajectoryStore(root / "trajectories", content),
                        settings=RuntimeSettings(
                            {
                                "default": {
                                    "kind": "remote",
                                    "config": {
                                        "host": "127.0.0.1",
                                        "port": port,
                                        "tls": False,
                                        "allowInsecure": True,
                                        "authTokenEnv": "RSI_WIRE_TEST_TOKEN",
                                    },
                                }
                            }
                        ),
                        role="task-agent",
                        base_url="http://127.0.0.1:1",
                        api_key="local",
                    )
                    model = ModelVersion("m", "fixture", await content.put_text("model"), now())
                    session = await runtime.open(
                        AgentSpec("task-agent", "areal-agent-runtime@v1", model)
                    )
                    try:
                        self.assertEqual(
                            await session.send("remote proof"),
                            "fake runtime handled 1 content block(s)",
                        )
                    finally:
                        await session.finish()
                    self.assertNotIn(
                        "local-wire-fixture", (session.directory / "request.json").read_text()
                    )
                finally:
                    if server.returncode is None:
                        server.terminate()
                    await server.wait()
