from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from areal_rsi.agents.research import DefaultResearchAgent
from areal_rsi.api.base import EvolutionTarget, ExperimentPlan, ExperimentRun, RunState
from areal_rsi.app import LocalApp
from areal_rsi.base.storage import write_json
from areal_rsi.config import ModelConfig, ModelConfigurationError
from areal_rsi.modules.benchmark import ArithmeticBenchmark
from areal_rsi.modules.harness import LocalHarnessRuntime
from areal_rsi.ui.cli import run_experiment


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


def plan_for(app: LocalApp) -> ExperimentPlan:
    return ExperimentPlan(
        "fixture goal",
        "harness-evolution",
        "arithmetic-v1",
        app.baseline.solution_id,
        EvolutionTarget.HARNESS,
        1,
        config=app.model_plan_config(),
    )


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


class LocalAppTest(unittest.IsolatedAsyncioTestCase):
    async def test_agent_roles_can_differ_while_research_agents_share_one_model(self) -> None:
        overrides = {
            "frontend": {"model": "frontend-model"},
            "rsi-agent": {"model": "rsi-agent-model"},
            "research-agent": {
                "model": "research-agent-model",
                "base_url": "https://research.example/v1",
                "api_key_env": "RESEARCH_CREDENTIAL",
            },
        }
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(
                "os.environ",
                {
                    **ROLE_ENV,
                    "RSI_AGENT_MODELS": json.dumps(overrides),
                    "RSI_RESEARCH_AGENT_COUNT": "2",
                    "RESEARCH_CREDENTIAL": "private-research-sentinel",
                },
                clear=True,
            ),
        ):
            app = await LocalApp.create(Path(temporary), lambda _: None)
            try:
                self.assertEqual(app.frontend_model_name, "frontend-model")
                self.assertEqual(app.rsi.model.name, "rsi-agent-model")
                researchers = app.service.context.research_agents
                for index in range(2):
                    research = researchers[index]
                    assert isinstance(research, DefaultResearchAgent)
                    self.assertEqual(research.model.name, "research-agent-model")
                    self.assertIs(research.runtime, app.agent_runtimes[research.agent_id])
                self.assertEqual(
                    app.agent_models["research-agent-1"],
                    app.agent_models["research-agent-2"],
                )
                self.assertIs(
                    app.agent_runtimes["research-agent-1"],
                    app.agent_runtimes["research-agent-2"],
                )
                self.assertEqual(app.baseline.model.name, "target-default-model-a")
                self.assertIsNot(
                    app.agent_runtimes["rsi-agent"], app.agent_runtimes["research-agent-1"]
                )
                metadata = json.dumps(app.model_plan_config())
                self.assertNotIn("private-research-sentinel", metadata)
                self.assertNotIn("research.example", metadata)
            finally:
                await app.close()

    async def test_frontend_changes_do_not_invalidate_a_backend_run(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict("os.environ", ROLE_ENV, clear=True),
        ):
            root = Path(temporary)
            first = await LocalApp.create(root, lambda _: None)
            plan = plan_for(first)
            await first.close()
            with patch.dict(
                "os.environ",
                {"RSI_AGENT_MODELS": '{"frontend":{"model":"frontend-model-b"}}'},
            ):
                second = await LocalApp.create(root, lambda _: None)
                try:
                    await second.bind_plan(plan)
                    self.assertEqual(second.frontend_model_name, "frontend-model-b")
                    self.assertEqual(second.baseline.solution_id, plan.baseline_solution_id)
                finally:
                    await second.close()

    async def test_unused_default_changes_do_not_invalidate_effective_bindings(self) -> None:
        overrides = {
            "rsi-agent": {"model": "rsi-agent-model"},
            "research-agent": {"model": "research-agent-model"},
        }
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(
                "os.environ",
                {
                    **ROLE_ENV,
                    "RSI_AGENT_MODELS": json.dumps(overrides),
                },
                clear=True,
            ),
        ):
            root = Path(temporary)
            first = await LocalApp.create(root, lambda _: None)
            plan = plan_for(first)
            await first.close()
            with patch.dict("os.environ", {"RSI_TEACHER_MODEL": "new-default"}):
                second = await LocalApp.create(root, lambda _: None)
                try:
                    await second.bind_plan(plan)
                    self.assertEqual(second.rsi.model.name, "rsi-agent-model")
                finally:
                    await second.close()

    async def test_per_line_research_model_override_is_rejected(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(
                "os.environ",
                {
                    **ROLE_ENV,
                    "RSI_RESEARCH_AGENT_COUNT": "2",
                },
                clear=True,
            ),
        ):
            root = Path(temporary)
            with (
                patch.dict(
                    "os.environ",
                    {"RSI_AGENT_MODELS": '{"research-agent-2":{"model":"other-model"}}'},
                ),
                self.assertRaisesRegex(ModelConfigurationError, "只支持"),
            ):
                await LocalApp.create(root, lambda _: None)

    async def test_fully_specified_research_agents_only_require_target_default(self) -> None:
        overrides = {
            agent: {"model": f"{agent}-model", "base_url": "http://127.0.0.1:1/v1"}
            for agent in ("rsi-agent", "research-agent")
        }
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(
                "os.environ",
                {
                    "RSI_AGENT_MODELS": json.dumps(overrides),
                    "RSI_TARGET_MODEL": "target-model",
                    "RSI_TARGET_BASE_URL": "http://127.0.0.1:2/v1",
                },
                clear=True,
            ),
        ):
            app = await LocalApp.create(Path(temporary), lambda _: None)
            try:
                self.assertEqual(app.rsi.model.name, "rsi-agent-model")
                self.assertEqual(app.baseline.model.name, "target-model")
                self.assertIsNone(app.frontend_model_name)
            finally:
                await app.close()

    async def test_teacher_changes_keep_target_baseline_and_target_changes_coexist(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict("os.environ", ROLE_ENV, clear=True),
        ):
            root = Path(temporary)
            first = await LocalApp.create(root, lambda _: None)
            original = first.baseline
            await first.close()
            with patch.dict("os.environ", {"RSI_TEACHER_MODEL": "teacher-default-model-b"}):
                second = await LocalApp.create(root, lambda _: None)
                try:
                    self.assertEqual(second.baseline, original)
                    self.assertEqual(second.rsi.model.name, "teacher-default-model-b")
                    for research in second.service.context.research_agents:
                        assert isinstance(research, DefaultResearchAgent)
                        self.assertEqual(research.model.name, "teacher-default-model-b")
                        self.assertIs(research.runtime, second.agent_runtimes["rsi-agent"])
                finally:
                    await second.close()
            with patch.dict("os.environ", {"RSI_TARGET_MODEL": "target-default-model-b"}):
                third = await LocalApp.create(root, lambda _: None)
                try:
                    self.assertNotEqual(third.baseline.solution_id, original.solution_id)
                    self.assertEqual(third.baseline.model.name, "target-default-model-b")
                    self.assertEqual(third.rsi.model.name, "teacher-default-model-a")
                finally:
                    await third.close()
            restored = await LocalApp.create(root, lambda _: None)
            try:
                self.assertEqual(restored.baseline, original)
                self.assertEqual(len(list((root / "baselines").glob("*.json"))), 2)
                self.assertFalse((root / "baseline.json").exists())
            finally:
                await restored.close()

    async def test_legacy_baseline_is_preserved_without_binding_the_directory(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict("os.environ", ROLE_ENV, clear=True),
        ):
            root = Path(temporary)
            first = await LocalApp.create(root, lambda _: None)
            original = first.baseline
            write_json(root / "baseline.json", original)
            await first.close()
            # 模拟只有旧版单基线文件和版本存储、尚未生成新索引的目录。
            for folder in (root / "model-index", root / "baselines"):
                for path in folder.glob("*.json"):
                    path.unlink()
            before = (root / "baseline.json").read_bytes()
            imported = await LocalApp.create(root, lambda _: None)
            try:
                self.assertEqual(imported.baseline, original)
            finally:
                await imported.close()
            with patch.dict("os.environ", {"RSI_TARGET_MODEL": "target-default-model-b"}):
                other = await LocalApp.create(root, lambda _: None)
                await other.close()
            self.assertEqual((root / "baseline.json").read_bytes(), before)

    async def test_run_binding_checks_target_but_does_not_bind_the_directory(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict("os.environ", ROLE_ENV, clear=True),
        ):
            root = Path(temporary)
            first = await LocalApp.create(root, lambda _: None)
            plan = plan_for(first)
            await first.close()
            with patch.dict("os.environ", {"RSI_TARGET_MODEL": "target-default-model-b"}):
                other = await LocalApp.create(root, lambda _: None)
                try:
                    with self.assertRaisesRegex(ModelConfigurationError, "RSI_TARGET"):
                        await other.bind_plan(plan)
                    self.assertEqual(other.agent_proxies["task-agent"].requests, [])
                finally:
                    await other.close()
            restored = await LocalApp.create(root, lambda _: None)
            try:
                await restored.bind_plan(plan)
                self.assertEqual(restored.baseline.solution_id, plan.baseline_solution_id)
            finally:
                await restored.close()

    async def test_cli_resume_cannot_send_an_old_run_to_a_different_target(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict("os.environ", ROLE_ENV, clear=True),
        ):
            root = Path(temporary)
            first = await LocalApp.create(root, lambda _: None)
            plan = plan_for(first)
            run = ExperimentRun("run_fixture", plan, RunState.PLANNED, plan.baseline_solution_id)
            first.service.records.save(run.run_id, run)
            await first.close()
            before = (root / "run-states/run_fixture.json").read_bytes()
            with (
                patch.dict(
                    "os.environ",
                    {
                        "RSI_DATA_ROOT": str(root),
                        "RSI_TARGET_MODEL": "target-default-model-b",
                    },
                ),
                self.assertRaisesRegex(ModelConfigurationError, "RSI_TARGET"),
            ):
                await run_experiment(None, run.run_id)
            self.assertEqual((root / "run-states/run_fixture.json").read_bytes(), before)

    async def test_run_can_keep_its_original_harness_baseline(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict("os.environ", ROLE_ENV, clear=True),
        ):
            app = await LocalApp.create(Path(temporary), lambda _: None)
            try:
                original = app.baseline
                harness = await app.versions.create_harness(
                    "older-harness", await app.content.put_json({"files": {}})
                )
                previous = await app.versions.create_solution(original.model, harness)
                plan = replace(plan_for(app), baseline_solution_id=previous.solution_id)
                await app.bind_plan(plan)
                self.assertEqual(app.baseline, previous)
                self.assertEqual(app.rsi.baseline, previous.solution_id)
            finally:
                await app.close()

    async def test_invalid_research_agent_count_fails_before_creating_services(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "new-run"
            for count in ("invalid", "0", "9", "1.5", "-1"):
                with (
                    self.subTest(count=count),
                    patch.dict(
                        "os.environ",
                        {**ROLE_ENV, "RSI_RESEARCH_AGENT_COUNT": count},
                        clear=True,
                    ),
                ):
                    with self.assertRaisesRegex(
                        ModelConfigurationError, "RSI_RESEARCH_AGENT_COUNT"
                    ):
                        await LocalApp.create(root, lambda _: None)
                    self.assertFalse(root.exists())

    async def test_local_role_wiring_and_reopen_do_not_persist_credentials(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict("os.environ", ROLE_ENV, clear=True),
        ):
            root = Path(temporary)
            app = await LocalApp.create(root, lambda _: None)
            try:
                self.assertIsInstance(app.service.context.benchmark, ArithmeticBenchmark)
                harness = app.service.context.harness_runtime
                assert isinstance(harness, LocalHarnessRuntime)
                self.assertIs(harness.runtime, app.agent_runtimes["task-agent"])
                self.assertIs(app.rsi.runtime, app.agent_runtimes["rsi-agent"])
                self.assertEqual(app.rsi.model, app.agent_models["rsi-agent"])
                self.assertEqual(app.baseline.model.name, "target-default-model-a")
                self.assertEqual(
                    app.agent_runtimes["rsi-agent"].base_url, app.agent_proxies["rsi-agent"].url
                )
                self.assertEqual(
                    app.agent_runtimes["task-agent"].base_url, app.agent_proxies["task-agent"].url
                )
                self.assertNotEqual(
                    app.agent_proxies["rsi-agent"].url, app.agent_proxies["task-agent"].url
                )
                self.assertEqual(
                    app.agent_runtimes["rsi-agent"].dsh_root,
                    app.agent_runtimes["task-agent"].dsh_root,
                )
                self.assertEqual(app.agent_runtimes["rsi-agent"].dsh_root, root.resolve())
                baseline = app.baseline.solution_id
            finally:
                await app.close()
            reopened = await LocalApp.create(root, lambda _: None)
            try:
                self.assertEqual(reopened.baseline.solution_id, baseline)
            finally:
                await reopened.close()
            for path in root.rglob("*"):
                if path.is_file():
                    for secret in (b"teacher-only-sentinel", b"target-only-sentinel"):
                        self.assertNotIn(secret, path.read_bytes(), str(path))

    async def test_missing_connection_does_not_create_run_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.dict("os.environ", {}, clear=True):
            root = Path(temporary) / "new-run"
            with self.assertRaises(ValueError):
                await LocalApp.create(root, lambda _: None)
            self.assertFalse(root.exists())
