"""Offline preflight checks for the example's one-step comparison."""

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


class ResearchPreflightTest(unittest.TestCase):
    def test_same_settings_required(self):
        example = Path(__file__).resolve().parents[1] / "examples/3d_bench"
        with patch.object(sys, "path", [str(example), *sys.path]):
            spec = importlib.util.spec_from_file_location(
                "research_example", example / "run_research.py"
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        snapshot = {"commit": "fixed", "files": {"model.js": "digest"}}
        inputs = {
            "snapshot": snapshot,
            "max_tokens": 4096,
            "timeout_seconds": 600,
            "generation_environment": module.GENERATION_ENVIRONMENT,
        }
        module.check_baseline_settings(inputs, snapshot)
        for environment in (None, "legacy"):
            with (
                self.subTest(environment=environment),
                self.assertRaisesRegex(ValueError, "environment"),
            ):
                module.check_baseline_settings(
                    {**inputs, "generation_environment": environment}, snapshot
                )
        with self.assertRaisesRegex(ValueError, "snapshot"):
            module.check_baseline_settings(inputs, {"commit": "changed"})
        for key in ("max_tokens", "timeout_seconds"):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "settings"):
                module.check_baseline_settings({**inputs, key: 1}, snapshot)
