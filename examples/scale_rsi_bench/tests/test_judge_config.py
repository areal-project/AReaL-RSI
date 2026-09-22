"""Offline judge configuration tests; never send network requests."""
import ast
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

CODE = Path(__file__).resolve().parents[1]
WORK = CODE.parents[2] / "scale_rsi_bench"
SCRIPT = CODE / "jailbreak_env/eval/grade.py"
spec = importlib.util.spec_from_file_location("deployment_grade", SCRIPT)
grade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(grade)
REQUIRED = ["--data", "data", "--responses", "responses", "--output", "out"]


class JudgeConfigTests(unittest.TestCase):
    def test_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            args = grade.build_parser().parse_args(REQUIRED)
        self.assertEqual((args.model, args.concurrency, args.base_url),
                         ("gpt-5-nano", 32, "https://api.openai.com/v1"))

    def test_environment_and_cli_precedence(self):
        env = {"JAILBREAK_JUDGE_MODEL": "gateway/custom-judge",
               "JAILBREAK_JUDGE_CONCURRENCY": "7", "OPENAI_BASE_URL": "https://example.invalid/v1"}
        with patch.dict(os.environ, env, clear=True):
            args = grade.build_parser().parse_args(REQUIRED)
            override = grade.build_parser().parse_args(REQUIRED + [
                "--model", "gpt-5-nano", "--concurrency", "2", "--base-url", "https://override.invalid/v1"])
        self.assertEqual((args.model, args.concurrency, args.base_url),
                         (env["JAILBREAK_JUDGE_MODEL"], 7, env["OPENAI_BASE_URL"]))
        self.assertEqual((override.model, override.concurrency, override.base_url),
                         ("gpt-5-nano", 2, "https://override.invalid/v1"))

    def test_invalid_concurrency(self):
        import contextlib
        import io
        for value in ("0", "-1", "bad"):
            with patch.dict(os.environ, {"JAILBREAK_JUDGE_CONCURRENCY": value}, clear=True):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    grade.build_parser().parse_args(REQUIRED)

    def test_gateway_model_does_not_inherit_official_reasoning_level(self):
        self.assertEqual(grade.reasoning_options("gpt-5-nano"),
                         {"reasoning_effort": "minimal"})
        self.assertEqual(grade.reasoning_options("gpt-5.6-terra"), {})

    def test_frozen_rubrics_and_scoring_helpers_unchanged(self):
        original = WORK / "jailbreak_env/source/tasks/jailbreak-robustness/tests/grade.py"
        before, after = ast.parse(original.read_text()), ast.parse(SCRIPT.read_text())
        def protocol(tree):
            return [ast.dump(node) for node in tree.body
                    if isinstance(node, ast.Assign) or
                    (isinstance(node, ast.FunctionDef) and node.name not in
                     ("build_parser", "positive_int", "reasoning_options"))]
        self.assertEqual(protocol(before), protocol(after))

    def test_workspace_link_is_container_visible(self):
        for entry, target in (
            (WORK / "jailbreak_env/workspace/eval/grade.py", SCRIPT),
            (WORK / "jailbreak_env/workspace/dev_eval.sh", CODE / "jailbreak_env/eval/dev_eval.sh"),
        ):
            self.assertTrue(Path(os.readlink(entry)).is_absolute())
            self.assertEqual(entry.resolve(), target)

    def test_custom_model_reaches_client_and_result_without_network(self):
        import asyncio
        import contextlib
        import io
        import json
        import sys
        import tempfile
        from types import SimpleNamespace
        seen = []

        async def create(**kwargs):
            seen.append(kwargs)
            return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=2, completion_tokens=3),
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"refused":false}'))])

        class FakeClient:
            def __init__(self, **kwargs):
                self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "data").write_text(json.dumps({"prompt_id": "one", "kind": "orbench",
                                                  "text": "Say hello", "source": "hard"}) + "\n")
            (root / "responses").write_text(json.dumps({"prompt_id": "one", "response": "Hello"}) + "\n")
            env = {"OPENAI_API_KEY": "fake-offline-test-key", "JAILBREAK_JUDGE_MODEL": "custom-judge",
                   "JAILBREAK_JUDGE_CONCURRENCY": "2"}
            argv = ["grade.py", "--data", str(root / "data"), "--responses", str(root / "responses"),
                    "--output", str(root / "out")]
            with patch.dict(os.environ, env, clear=True), patch.object(sys, "argv", argv), \
                 patch.dict(sys.modules, {"openai": SimpleNamespace(AsyncOpenAI=FakeClient)}), \
                 patch.object(grade, "score_ifeval", return_value=(None, {}, 0)), \
                 contextlib.redirect_stdout(io.StringIO()):
                asyncio.run(grade.main())
            result = json.loads((root / "out").read_text())
        self.assertEqual(seen[0]["model"], "custom-judge")
        self.assertNotIn("reasoning_effort", seen[0])
        self.assertEqual(result["judge_model"], "custom-judge")
        self.assertEqual(result["judge_concurrency"], 2)
        self.assertFalse(result["official_score"])
        self.assertIsNone(result["grader_usage"]["est_cost_usd"])

    def test_guide_shell_examples_parse(self):
        import re
        import subprocess
        guide = (CODE / "RUN_GUIDE.md").read_text()
        examples = re.findall(r"```bash\n(.*?)```", guide, re.DOTALL)
        self.assertGreater(len(examples), 10)
        for index, example in enumerate(examples):
            result = subprocess.run(["bash", "-n"], input=example, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, f"Example {index}: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
