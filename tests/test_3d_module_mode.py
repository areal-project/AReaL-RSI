"""Syntax checks must use the evaluator's module mode without executing candidates."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class ModuleModeTest(unittest.TestCase):
    def setUp(self):
        example = Path(__file__).resolve().parents[1] / "examples/3d_bench"
        with patch.object(sys, "path", [str(example), *sys.path]):
            spec = importlib.util.spec_from_file_location(
                "baseline_example", example / "run_baseline.py"
            )
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)

    def template(self, root, package):
        template = root / "template"
        (template / "src").mkdir(parents=True)
        (template / "src/model.js").write_text("export function createModel() {}\n")
        (template / "CONTRACT_V2.md").write_text("Public contract")
        (template / "package.json").write_text(json.dumps(package))
        (template / "private-validator.js").write_text("Private verifier")
        return template

    def test_public_package_matches_template_and_excludes_verifier(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            template = self.template(root, {"type": "module", "private": True})
            workspace = root / "workspace"
            self.module.prepare_public_workspace(template, workspace)
            self.assertEqual(
                (workspace / "package.json").read_bytes(), (template / "package.json").read_bytes()
            )
            self.assertFalse((workspace / "private-validator.js").exists())

    def test_ambiguous_template_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            template = self.template(root, {})
            with self.assertRaisesRegex(ValueError, "type=module"):
                self.module.prepare_public_workspace(template, root / "workspace")

    @unittest.skipUnless(shutil.which("node"), "Node is required for syntax integration checks")
    def test_invalid_export_module_rejected_in_both_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            template = self.template(root, {"type": "module"})
            workspace = root / "workspace"
            self.module.prepare_public_workspace(template, workspace)
            sources = (
                ("export function createModel() { const x = [1, 2); }\n", False),
                (
                    "export function createModel() { return []; }\n"
                    "throw new Error('must not execute');\n",
                    True,
                ),
            )
            for source, valid in sources:
                for directory in (template, workspace):
                    with self.subTest(valid=valid, directory=directory.name):
                        (directory / "src/model.js").write_text(source)
                        checked = subprocess.run(
                            [shutil.which("node"), "--check", "src/model.js"],
                            cwd=directory,
                            env={"PATH": os.environ.get("PATH", "")},
                            capture_output=True,
                            text=True,
                            timeout=15,
                        )
                        self.assertEqual(checked.returncode == 0, valid, checked.stderr)
                        if not valid:
                            self.assertIn("SyntaxError", checked.stderr)
