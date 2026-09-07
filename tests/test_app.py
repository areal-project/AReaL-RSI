from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from areal_rsi.app import LocalApp
from areal_rsi.config import ModelConfig
from areal_rsi.modules.benchmark import ArithmeticBenchmark
from areal_rsi.modules.harness import LocalHarnessRuntime


class ModelConfigTest(unittest.TestCase):
    def test_connection_requires_explicit_model_and_service(self) -> None:
        for env in ({}, {"RSI_MODEL": "test-model"}, {"RSI_BASE_URL": "http://localhost:8000"}):
            with (
                self.subTest(env=env),
                patch.dict("os.environ", env, clear=True),
                self.assertRaisesRegex(ValueError, "RSI_MODEL 和 RSI_BASE_URL"),
            ):
                ModelConfig.from_environment()

    def test_private_url_parts_are_rejected_without_echoing_them(self) -> None:
        for url in (
            "file:///tmp/model",
            "https://user:sentinel@model.example/v1",
            "https://model.example/v1?key=sentinel",
            "https://model.example/v1#sentinel",
            "http://localhost:invalid/v1",
        ):
            with self.subTest(url=url), patch.dict(
                "os.environ", {"RSI_MODEL": "test-model", "RSI_BASE_URL": url}, clear=True
            ):
                with self.assertRaises(ValueError) as error:
                    ModelConfig.from_environment()
                self.assertNotIn("sentinel", str(error.exception))

    def test_credentials_are_optional_and_hidden_from_repr(self) -> None:
        env = {"RSI_MODEL": "test-model", "RSI_BASE_URL": "http://localhost:8000/"}
        with patch.dict("os.environ", env, clear=True):
            public = ModelConfig.from_environment()
            self.assertEqual(public.api_key, "")
            self.assertEqual(public.base_url, "http://localhost:8000/v1")
        with patch.dict("os.environ", {**env, "RSI_API_KEY": "test-only-sentinel"}, clear=True):
            private = ModelConfig.from_environment()
            self.assertEqual(private.api_key, "test-only-sentinel")
            self.assertNotIn("test-only-sentinel", repr(private))


class LocalAppTest(unittest.IsolatedAsyncioTestCase):
    async def test_local_startup_and_reopen_do_not_persist_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            "os.environ",
            {
                "RSI_MODEL": "test-model",
                "RSI_BASE_URL": "http://127.0.0.1:1/v1",
                "RSI_API_KEY": "test-only-sentinel",
            },
            clear=True,
        ):
            root = Path(temporary)
            app = await LocalApp.create(root, lambda _: None)
            try:
                self.assertIsInstance(app.service.context.benchmark, ArithmeticBenchmark)
                self.assertIsInstance(app.service.context.harness_runtime, LocalHarnessRuntime)
                baseline = app.baseline.solution_id
                self.assertEqual(app.proxy.requests, [])
            finally:
                await app.close()
            reopened = await LocalApp.create(root, lambda _: None)
            try:
                self.assertEqual(reopened.baseline.solution_id, baseline)
            finally:
                await reopened.close()
            for path in root.rglob("*"):
                if path.is_file():
                    self.assertNotIn(b"test-only-sentinel", path.read_bytes(), str(path))

    async def test_missing_connection_does_not_create_run_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.dict("os.environ", {}, clear=True):
            root = Path(temporary) / "new-run"
            with self.assertRaises(ValueError):
                await LocalApp.create(root, lambda _: None)
            self.assertFalse(root.exists())
