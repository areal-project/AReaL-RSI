from __future__ import annotations

import unittest
from unittest.mock import patch

from areal_rsi.config import ModelConfig, ModelConfigurationError


class ModelConfigTest(unittest.TestCase):
    def test_connection_requires_explicit_model_and_service(self) -> None:
        for env in (
            {},
            {"RSI_MODEL": "legacy-teacher-model"},
            {"RSI_BASE_URL": "http://localhost:8000"},
        ):
            with (
                self.subTest(env=env),
                patch.dict("os.environ", env, clear=True),
                self.assertRaisesRegex(ValueError, "MODEL 和 .*BASE_URL"),
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
            with (
                self.subTest(url=url),
                patch.dict(
                    "os.environ",
                    {"RSI_MODEL": "legacy-teacher-model", "RSI_BASE_URL": url},
                    clear=True,
                ),
            ):
                with self.assertRaises(ValueError) as error:
                    ModelConfig.from_environment()
                self.assertNotIn("sentinel", str(error.exception))

    def test_credentials_are_optional_and_hidden_from_repr(self) -> None:
        env = {
            "RSI_MODEL": "legacy-teacher-model",
            "RSI_BASE_URL": "http://localhost:8000/",
        }
        with patch.dict("os.environ", env, clear=True):
            public = ModelConfig.from_environment()
            self.assertEqual(public.api_key, "")
            self.assertEqual(public.base_url, "http://localhost:8000/v1")
        with patch.dict("os.environ", {**env, "RSI_API_KEY": "test-only-sentinel"}, clear=True):
            private = ModelConfig.from_environment()
            self.assertEqual(private.api_key, "test-only-sentinel")
            self.assertNotIn("test-only-sentinel", repr(private))

ROLE_ENV = {
    "RSI_TEACHER_MODEL": "teacher-default-model-a",
    "RSI_TEACHER_BASE_URL": "http://127.0.0.1:1/v1",
    "RSI_TEACHER_API_KEY": "teacher-only-sentinel",
    "RSI_TARGET_MODEL": "target-default-model-a",
    "RSI_TARGET_BASE_URL": "http://127.0.0.1:2/v1",
    "RSI_TARGET_API_KEY": "target-only-sentinel",
}

class ModelRolesTest(unittest.TestCase):
    def test_roles_do_not_borrow_models_endpoints_or_credentials(self) -> None:
        with patch.dict("os.environ", ROLE_ENV, clear=True):
            teacher = ModelConfig.from_environment("teacher")
            target = ModelConfig.from_environment("target")
            self.assertEqual(teacher.model, "teacher-default-model-a")
            self.assertEqual(target.model, "target-default-model-a")
            self.assertNotEqual(teacher.base_url, target.base_url)
            self.assertEqual(teacher.api_key, "teacher-only-sentinel")
            self.assertEqual(target.api_key, "target-only-sentinel")
            self.assertNotIn("api_key", target.public_identity())
        env = {key: value for key, value in ROLE_ENV.items() if key != "RSI_TARGET_API_KEY"}
        with patch.dict("os.environ", env, clear=True):
            self.assertEqual(ModelConfig.from_environment("target").api_key, "")
        with patch.dict(
            "os.environ",
            {
                "RSI_MODEL": "legacy-teacher-model",
                "RSI_BASE_URL": "http://localhost",
            },
            clear=True,
        ):
            self.assertEqual(ModelConfig.from_environment("teacher").model, "legacy-teacher-model")
            with self.assertRaisesRegex(ModelConfigurationError, "RSI_TARGET_MODEL"):
                ModelConfig.from_environment("target")
