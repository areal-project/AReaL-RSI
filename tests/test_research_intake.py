"""Conversation intake, actual command binding, and the start boundary."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from runtime_fakes import ActionRuntime
from test_research_runtime_package import ResponsesModel

from areal_rsi.api.interfaces import ToolCall
from areal_rsi.api.research import TaskBrief
from areal_rsi.infra.coordination.config import config_from_dict
from areal_rsi.infra.coordination.engine import ResearchEngine
from areal_rsi.infra.coordination.policies.passk import PassKActions, launch
from areal_rsi.infra.coordination.tools.brief import PASSK_HIDDEN_LIMITS
from areal_rsi.infra.execution import ApprovedCommand
from areal_rsi.infra.research import ResearchSystem
from areal_rsi.infra.storage.files import encode


def system_at(root: Path) -> ResearchSystem:
    return ResearchSystem(root, runtime_factory=lambda _: ActionRuntime("wait"))


@pytest.mark.parametrize("backend,modules", [
    ("codex", ("leader_guidance",)),
    pytest.param("standard", (), marks=pytest.mark.skipif(
        os.environ.get("RSI_TEST_NATIVE_HARNESSES") != "1", reason="Opt in to native DSH"
    )),
])
def test_research_prompt_selection_reaches_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str, modules: tuple[str, ...]
) -> None:
    monkeypatch.setenv("RSI_API_KEY", "fixture")
    model = ResponsesModel()

    async def idle(self: ResearchEngine) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(ResearchEngine, "serve", idle)

    async def scenario() -> None:
        system = ResearchSystem(tmp_path)
        try:
            config: dict[str, Any] = {
                "brief": {"objective": "检查研究说明", "instructions": "整理已有资料"},
                "backend": backend,
                "base_url": model.url,
                "model_settings_pinned": True,
            }
            config["collaboration_modules"] = list(modules)
            detail = await system.create({"config": config})
            c = system.coordinator(detail["run_id"])
            active = await system.attach(c)
            assert system.config(c).collaboration_modules == modules
            c.start()
            c.begin_turn("rsi")
            assert isinstance(active.actions, PassKActions)
            await launch(c, active.actions)
            researcher = c.store.run().research_agents[0]
            c.end_turn("rsi")
            await active.engine._drive(researcher.agent_id)
            assert len(model.requests) == 1
            request = model.requests[0]
            prompt = json.dumps(request, ensure_ascii=False)
            assert "你是 Research Agent" in prompt
            assert "{{tool_instructions}}" not in prompt
            assert ("read_researcher_trace" in prompt) == bool(modules)
            assert ("Codex 原生网页工具" in prompt) == (backend == "codex")
            assert prompt.count("你的默认工作区：") == 1
            assert prompt.count("不允许以任何方式作弊") == 1
            assert c.actor(researcher.agent_id).wait_reason != "rsi_reply"
        finally:
            await system.close()

    try:
        asyncio.run(scenario())
    finally:
        model.close()


def test_unknown_research_prompt_variant_is_rejected() -> None:
    with pytest.raises(ValueError, match="已移除"):
        config_from_dict(
            {"brief": {"objective": "研究", "instructions": ""}, "research_prompt_variant": "typo"}
        )


def test_conversation_can_bind_real_cpu_execution_without_template_or_baseline(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        system = system_at(tmp_path)
        draft = await system.create({})
        c = system.coordinator(draft["run_id"])
        assert not draft["simulated"] and draft["status"] == "planned"
        assert draft["brief"]["verification"] is None
        assert draft["nodes"] == [] and not system.active
        with pytest.raises(ValueError, match="确认"):
            await system.call("start", {"run_id": c.store.run_id})
        active = await system.attach(c)
        tools = active.actions.handler("interaction")
        brief = TaskBrief("研究文本处理", "比较规则与结果，保留具体观察")
        brief.limits.max_agents = 3
        arguments = encode(brief)
        for name in PASSK_HIDDEN_LIMITS:
            arguments["limits"].pop(name)
        result = await tools(ToolCall("update_brief", arguments, "brief"))
        assert "error" not in result.output
        command = ApprovedCommand(
            "inspect",
            (
                sys.executable,
                "-c",
                "import os,sys,pathlib; "
                "pathlib.Path(sys.argv[1], 'result.txt').write_text(os.environ['RSI_TEST_VALUE'])",
                "{output}",
            ),
            ("output",),
            timeout_seconds=10,
        )
        configured = await tools(
            ToolCall(
                "set_execution",
                {
                    "execution_json": json.dumps(
                        {
                            "commands": [encode(command)],
                            "execution_env": {"RSI_TEST_VALUE": "真实进程输出"},
                            "description": "本机 CPU，不需要起点或验证分数",
                        }
                    )
                },
                "execution",
            )
        )
        assert configured.output["issues"] == []
        ready = await tools(
            ToolCall(
                "prepare_research",
                {
                    "summary": "研究文本处理，使用本机 CPU，最多三个研究方向。",
                    "execution_required": True,
                },
                "prepare",
            )
        )
        assert ready.stop and ready.output["ready"]
        try:
            await system.call("start", {"run_id": c.store.run_id})
            assert active.engine.max_concurrent_turns == 5
            assert c.state.seed_candidate_id is None
            frozen = json.loads((c.store.root / "coordination/started-config.json").read_text())
            assert frozen["brief"]["objective"] == brief.objective
            assert frozen["execution_env"]["RSI_TEST_VALUE"] == "真实进程输出"
            await active.actions.handler("rsi")(ToolCall("start_researchers", {}, "spawn"))
            researcher = c.store.run().research_agents[0].agent_id
            job = await active.actions.handler(researcher)(
                ToolCall(
                    "run_job",
                    {
                        "command_name": "inspect",
                        "candidate_id": "",
                        "parameters_json": "{}",
                    },
                    "execute",
                )
            )
            job_id = job.output["job_id"]
            for _ in range(200):
                await active.execution.poll()
                record = active.execution.manager.get(job_id)
                if record is not None and record.state == "succeeded":
                    break
                await asyncio.sleep(0.02)
            assert record is not None and record.state == "succeeded"
            assert record.spec.backend == "local"
            output = Path(active.execution.jobs[job_id].output) / "result.txt"
            assert output.read_text() == "真实进程输出"
            blocked = await tools(ToolCall("set_execution", {"execution_json": "{}"}, "late"))
            assert "not available" in blocked.output["error"]
            assert (await system.detail(c.store.run_id))["best"] is None
            with pytest.raises(ValueError, match="先停止"):
                await system.call("delete", {"run_id": c.store.run_id})
            await system.call("stop", {"run_id": c.store.run_id})
            await system.call("delete", {"run_id": c.store.run_id})
            assert not c.store.root.exists()
        finally:
            await system.close()

    asyncio.run(scenario())


def test_invalid_passk_brief_does_not_corrupt_draft(tmp_path: Path) -> None:
    async def scenario() -> None:
        system = system_at(tmp_path)
        try:
            draft = await system.create(
                {"conversation": True, "collaboration": {"mode": "passk", "modules": []}}
            )
            c = system.coordinator(draft["run_id"])
            active = await system.attach(c)
            path = c.store.root / "coordination/config.json"
            original_config = path.read_bytes()
            original_run = c.store.run()
            brief = c.store.brief()
            brief.limits.max_total_tokens = 50_000_000
            result = await active.actions.handler("interaction")(
                ToolCall("update_brief", encode(brief), "invalid")
            )
            assert "Additional properties" in str(result.output)
            assert path.read_bytes() == original_config
            assert c.store.run() == original_run
            detail = await system.detail(c.store.run_id)
            assert detail["collaboration"]["mode"] == "passk"
            arguments = encode(brief)
            for key in PASSK_HIDDEN_LIMITS:
                arguments["limits"].pop(key)
            arguments["limits"]["max_agents"] = 8
            arguments["limits"]["max_jobs"] = 8
            result = await active.actions.handler("interaction")(
                ToolCall("update_brief", arguments, "valid")
            )
            assert "error" not in result.output
            saved = system.config(c)
            assert saved.brief.limits.max_agents == 8
            for key in PASSK_HIDDEN_LIMITS:
                assert getattr(saved.brief.limits, key) is None
        finally:
            await system.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("execution_required", [True, False])
def test_passk_handoff_accepts_optional_execution(tmp_path: Path, execution_required: bool) -> None:
    from areal_rsi.api.research import VerificationContract
    from areal_rsi.infra.coordination.intake import validate_execution
    from areal_rsi.infra.storage.files import write_json

    async def scenario() -> None:
        system = system_at(tmp_path)
        try:
            draft = await system.create(
                {"conversation": True, "collaboration": {"mode": "passk", "modules": []}}
            )
            c = system.coordinator(draft["run_id"])
            config = system.config(c)
            config.brief.objective = "Improve external benchmark"
            config.brief.instructions = (
                "Use the existing submit/status scripts and report their score"
            )
            if execution_required:
                config.brief.verification = VerificationContract("ppl", "minimize")
                config.commands = (ApprovedCommand("submit", (sys.executable, "-c", "pass")),)
            assert validate_execution(config) == []
            if config.brief.verification:
                config.brief.verification.target = 10
                assert validate_execution(config)  # Trusted automatic target needs a verifier.
                config.brief.verification.target = None
            run = c.store.run()
            run.brief_ref = c.store.put("briefs", "configured", config.brief)
            c.store.save_run(run)
            write_json(c.store.root / "coordination/config.json", config.public())
            active = await system.attach(c)
            c.enqueue("user", "interaction", "Start the benchmark", key="start")
            message, _ = c.begin_turn("interaction")
            result = await active.actions.handler("interaction")(
                ToolCall(
                    "handoff_research",
                    {
                        "confirmation_message_id": message.message_id,
                        "summary": "Start with existing experiment commands",
                        "execution_required": execution_required,
                    },
                    "handoff",
                )
            )
            assert result.output == {"started": True}
            assert c.state.status == "running"
            assert c.state.execution_setup == "ready"
            assert c.state.started_at
            assert active.execution.verifier is None
            assert (c.store.root / "coordination/started-config.json").exists()
        finally:
            await system.close()

    asyncio.run(scenario())
