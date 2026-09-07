import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml  # type: ignore[import-untyped]

from areal_rsi.api.base import AgentSpec, ModelVersion
from areal_rsi.base.storage import FileContentStore, FileTrajectoryStore, now
from areal_rsi.modules.runtime import DshAgentRuntime


class RuntimeRegistryTest(unittest.IsolatedAsyncioTestCase):
    async def test_only_registered_tools_are_assembled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            content = FileContentStore(root / "content")
            trajectories = FileTrajectoryStore(root / "trajectories", content)
            model = ModelVersion("model_fixture", "fixture", await content.put_text("model"), now())
            runtime = DshAgentRuntime(
                root / "sessions",
                content,
                trajectories,
                base_url="http://127.0.0.1:1",
                api_key="fixture",
                tool_registry={"custom@v1": [{"id": "custom", "name": "test-package"}]},
            )
            with self.assertRaisesRegex(ValueError, "未注册工具"):
                await runtime.open(
                    AgentSpec("agent", "deepseek-harness@v1", model, tools=("memory@v1",))
                )
            with (
                patch.dict(os.environ, {"RSI_API_KEY": "host-only-sentinel"}),
                patch("areal_rsi.modules.runtime.DeepSeekHarness") as sdk,
                patch(
                    "areal_rsi.modules.runtime.ensure_cli", return_value=root / "shared-dsh"
                ) as ensure,
                patch(
                    "areal_rsi.modules.runtime.sandbox_wrapper",
                    side_effect=lambda directory, _workspace, _cli: directory / "dsh",
                ) as sandbox,
            ):
                session = await runtime.open(
                    AgentSpec("agent", "deepseek-harness@v1", model, tools=("custom@v1",))
                )
                spec = yaml.safe_load(Path(sdk.call_args.kwargs["patches"][0]).read_text())
                self.assertEqual(sdk.call_args.kwargs["env"]["RSI_API_KEY"], "")
                self.assertEqual(os.environ["RSI_API_KEY"], "host-only-sentinel")
                self.assertIn({"insert": [{"id": "custom", "name": "test-package"}]}, spec)
                ensure.assert_called_once_with(root)
                self.assertEqual(sandbox.call_args.args[2], root / "shared-dsh")
                self.assertEqual(
                    sdk.call_args.kwargs["dsh_bin"], str(sandbox.call_args.args[0] / "dsh")
                )
                await session.finish()
