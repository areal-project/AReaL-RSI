"""Conversation intake, actual command binding, and the start boundary."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from areal_rsi.api.research import TaskBrief
from areal_rsi.base.storage import encode
from areal_rsi.coordination.config import config_from_dict
from areal_rsi.coordination.engine import ResearchEngine
from areal_rsi.execution import ApprovedCommand
from areal_rsi.modules.responses import ResponsesConfig, ResponsesRuntime, ToolCall
from areal_rsi.research_app import ResearchApp


class IdleTransport:
    async def request(self, config: Any, payload: Any, sink: Any) -> dict[str, Any]:
        return {
            "id": "idle",
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "name": "wait",
                    "call_id": "wait",
                    "arguments": '{"reason":"等待外部研究输入"}',
                }
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }


def app_at(root: Path) -> ResearchApp:
    return ResearchApp(
        root,
        runtime_factory=lambda _: ResponsesRuntime(
            ResponsesConfig(base_url="https://example.test", api_key="test"), IdleTransport()
        ),
    )


@pytest.mark.parametrize("backend", ["standard", "codex"])
@pytest.mark.parametrize("variant", [None, "simple", "detailed"])
def test_research_prompt_selection_reaches_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str, variant: str | None
) -> None:
    monkeypatch.setenv("RSI_API_KEY", "fixture")
    payloads: list[dict[str, Any]] = []

    class RecordingTransport:
        async def request(self, config: Any, payload: Any, sink: Any) -> dict[str, Any]:
            payloads.append(payload)
            return {
                "id": "report",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "已完成本阶段的资料整理。"}],
                    }
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            }

    async def idle(self: ResearchEngine) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(ResearchEngine, "serve", idle)

    async def scenario() -> None:
        app = ResearchApp(
            tmp_path,
            runtime_factory=lambda _: ResponsesRuntime(
                ResponsesConfig(base_url="https://example.test", api_key="fixture"),
                RecordingTransport(),
            ),
        )
        try:
            config: dict[str, Any] = {
                "brief": {"objective": "检查研究说明", "instructions": "整理已有资料"},
                "backend": backend,
            }
            if variant is not None:
                config["research_prompt_variant"] = variant
            detail = await app.create({"config": config})
            c = app.coordinator(detail["run_id"])
            active = await app.attach(c)
            assert app.config(c).research_prompt_variant == (variant or "simple")
            c.start()
            c.begin_turn("rsi")
            researcher = c.spawn(key="researcher", direction="资料", instruction="整理资料")
            c.end_turn("rsi")
            await active.engine._drive(researcher["agent_id"])
            assert len(payloads) == 1
            prompt = payloads[0]["instructions"]
            assert prompt.startswith("你是 Research Agent")
            assert "{{tool_instructions}}" not in prompt
            assert ("二、判断实验结果" in prompt) == (variant == "detailed")
            assert ("Codex 原生网页工具" in prompt) == (backend == "codex")
            assert prompt.count("你的默认工作区：") == 1
            assert prompt.count("不允许以任何方式作弊") == 1
            assert "message_researcher" in prompt and "assign" in prompt
            assert c.actor(researcher["agent_id"]).wait_reason == "rsi_reply"
        finally:
            await app.close()

    asyncio.run(scenario())


def test_unknown_research_prompt_variant_is_rejected() -> None:
    with pytest.raises(ValueError, match="simple 或 detailed"):
        config_from_dict(
            {"brief": {"objective": "研究", "instructions": ""}, "research_prompt_variant": "typo"}
        )


def test_conversation_can_bind_real_cpu_execution_without_template_or_baseline(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        app = app_at(tmp_path)
        draft = await app.create({})
        c = app.coordinator(draft["run_id"])
        assert not draft["simulated"] and draft["status"] == "planned"
        assert draft["brief"]["verification"] is None
        assert draft["nodes"] == [] and not app.active
        with pytest.raises(ValueError, match="确认"):
            await app.call("start", {"run_id": c.store.run_id})
        active = await app.attach(c)
        tools = active.actions.handler("interaction")
        brief = TaskBrief("研究文本处理", "比较规则与结果，保留具体观察")
        brief.limits.max_agents = 3
        await tools(ToolCall("update_brief", {"brief_json": json.dumps(encode(brief))}, "brief"))
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
            await app.call("start", {"run_id": c.store.run_id})
            assert active.engine.max_concurrent_turns == 5
            assert c.state["seed_candidate_id"] is None
            frozen = json.loads((c.store.root / "coordination/started-config.json").read_text())
            assert frozen["brief"]["objective"] == brief.objective
            assert frozen["execution_env"]["RSI_TEST_VALUE"] == "真实进程输出"
            job = await active.actions.handler("rsi")(
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
            output = Path(active.execution.jobs[job_id]["output"]) / "result.txt"
            assert output.read_text() == "真实进程输出"
            blocked = await tools(ToolCall("set_execution", {"execution_json": "{}"}, "late"))
            assert "已经开始" in blocked.output["error"]
            assert (await app.detail(c.store.run_id))["best"] is None
            with pytest.raises(ValueError, match="先停止"):
                await app.call("delete", {"run_id": c.store.run_id})
            await app.call("stop", {"run_id": c.store.run_id})
            await app.call("delete", {"run_id": c.store.run_id})
            assert not c.store.root.exists()
        finally:
            await app.close()

    asyncio.run(scenario())
