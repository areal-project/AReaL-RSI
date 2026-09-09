"""Conversation intake, actual command binding, and the start boundary."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from areal_rsi.api.research import ResearchMessage, TaskBrief, VerificationContract
from areal_rsi.base.storage import encode
from areal_rsi.coordination.actions import ResearchActions
from areal_rsi.coordination.config import ResearchConfig
from areal_rsi.coordination.intake import ResearchIntake, validate_execution
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
        finally:
            await app.close()

    asyncio.run(scenario())


def test_only_reply_to_current_summary_can_start_and_edits_invalidate_readiness(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        app = app_at(tmp_path)
        c = app.coordinator((await app.create({}))["run_id"])
        actions = ResearchActions(c)

        async def start(_: str | None) -> None:
            c.start()

        ResearchIntake(actions, repository=app.repository, start=start)
        tools = actions.handler("interaction")
        old = c.enqueue("user", "interaction", "想研究一种方法", key="old")
        c.begin_turn("interaction")
        unready = await tools(
            ToolCall(
                "prepare_research",
                {
                    "summary": "尚未聊清楚",
                    "execution_required": True,
                },
                "missing",
            )
        )
        assert not unready.output["ready"] and len(unready.output["issues"]) == 2
        brief = TaskBrief("研究一种方法", "先整理方法和证据，不运行实验")
        brief.limits.max_verify_calls = 0
        await tools(ToolCall("update_brief", {"brief_json": json.dumps(encode(brief))}, "brief"))
        prepare = ToolCall(
            "prepare_research",
            {
                "summary": "先整理方法和证据，无需起点或实验。",
                "execution_required": False,
            },
            "prepare",
        )
        assert (await tools(prepare)).stop
        assert (await tools(prepare)).stop
        summaries = [
            m for m in c.store.records("messages", ResearchMessage) if m.sender == "interaction"
        ]
        assert len(summaries) == 1
        refused = await tools(ToolCall("start_research", {"confirmation_message_id": old}, "early"))
        assert "明确回复" in refused.output["error"]
        brief.instructions += "，报告不超过两页"
        await tools(ToolCall("update_brief", {"brief_json": json.dumps(encode(brief))}, "changed"))
        assert c.state["intake"]["status"] == "discussing"
        with pytest.raises(ValueError, match="确认"):
            c.start()
        await tools(ToolCall("prepare_research", prepare.arguments, "prepare_again"))
        c.end_turn("interaction")
        approved = c.enqueue("user", "interaction", "开始吧", key="approved")
        c.begin_turn("interaction")
        result = await tools(
            ToolCall("start_research", {"confirmation_message_id": approved}, "start")
        )
        assert result.output["started"] and c.state["status"] == "running"
        assert "prepare_research" not in {d["name"] for d in actions.definitions("rsi")}

    asyncio.run(scenario())


def test_formal_workbench_separates_old_simulations_without_rewriting_records(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        old = ResearchApp(
            tmp_path,
            config=ResearchConfig(TaskBrief("测试记录", "保留来源")),
            allow_simulation=True,
        )
        simulated = await old.create({"simulated": True})
        app = app_at(tmp_path)
        real = await app.create({})
        assert [r["run_id"] for r in (await app.call("list", {}))["runs"]] == [real["run_id"]]
        with pytest.raises(ValueError, match="开发测试"):
            await app.call("detail", {"run_id": simulated["run_id"]})
        assert old.coordinator(simulated["run_id"]).state["simulated"]

    asyncio.run(scenario())


def test_verification_and_credential_requirements_are_not_bypassed(tmp_path: Path) -> None:
    config = ResearchConfig(
        TaskBrief("研究", "实际验证", verification=VerificationContract("accuracy"))
    )
    assert any("验证程序" in issue for issue in validate_execution(config))
    config.execution_env = {"RSI_TARGET_API_KEY": "private-key-value"}
    with pytest.raises(ValueError, match="密钥"):
        validate_execution(config)
    config.execution_env = {}
    config.verifier = ApprovedCommand("verify", (sys.executable,), gpu_count=2)
    issues = validate_execution(config)
    assert any("GPU" in issue for issue in issues)
    assert any("身份" in issue for issue in issues)
    config.commands = (ApprovedCommand("run", (sys.executable, "experiment.py", "{output_dir}")),)
    assert any("未在 parameters 中声明" in issue for issue in validate_execution(config))


def test_import_keeps_user_goal_budget_and_narrower_edit_scope(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = app_at(tmp_path / "data")
        c = app.coordinator((await app.create({}))["run_id"])
        actions = ResearchActions(c)
        intake = ResearchIntake(actions, repository=app.repository, start=lambda _: app.start(c))
        brief = TaskBrief("用户的研究目标", "用户的方向建议")
        brief.limits.allowed_paths = ("skills/*",)
        brief.limits.max_nodes = 5
        await actions.handler("interaction")(
            ToolCall("update_brief", {"brief_json": json.dumps(encode(brief))}, "brief")
        )
        supplied = ResearchConfig(
            TaskBrief(
                "配置里的旧标题", "执行命令用法", verification=VerificationContract("accuracy")
            ),
            model="different-model",
            verifier=ApprovedCommand("verify", (sys.executable,)),
            metadata={"verification_identity": {"adapter": "content", "phases": {}}},
        )
        path = tmp_path / "execution.json"
        path.write_text(json.dumps(supplied.public()))
        imported = await actions.handler("interaction")(
            ToolCall("load_execution_config", {"path": str(path)}, "import")
        )
        assert "error" not in imported.output
        result = intake.config()
        assert result.brief.objective == "用户的研究目标"
        assert result.brief.instructions == "用户的方向建议"
        assert result.brief.limits.allowed_paths == ("skills/*",)
        assert result.brief.limits.max_nodes == 5
        assert result.model == "gpt-6-astra"
        assert result.metadata["execution_instructions"] == "执行命令用法"
        assert result.brief.verification is not None
        assert len(list((c.store.root / "coordination/configurations").glob("*.json"))) == 1

    asyncio.run(scenario())


def test_kernel_preparation_reads_standard_selection_file_and_pins_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from areal_rsi.coordination.preparation import prepare_example

    async def scenario() -> None:
        app = app_at(tmp_path / "data")
        c = app.coordinator((await app.create({}))["run_id"])
        selections = tmp_path / "selection.json"
        tasks = {
            s: [{"level": 1, "problem_id": n}]
            for n, s in enumerate(("development", "search", "final"), 1)
        }
        selections.write_text(json.dumps({"revision": "a" * 40, "tasks": tasks}))
        prepared_calls = []

        def prepare(repository: Path, destination: Path, selected: Any, *, revision: str) -> None:
            prepared_calls.append((repository, selected, revision))
            destination.mkdir()

        class Process:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                return b"", b""

        async def spawn(*command: str, **kwargs: Any) -> Process:
            assert command[0] == sys.executable and "--allow-local" not in command
            output = Path(command[command.index("--output") + 1])
            output.write_text(
                json.dumps(ResearchConfig(TaskBrief("配置目标", "环境说明")).public())
            )
            return Process()

        monkeypatch.setattr("areal_rsi.adapters.kernelbench.prepare_dataset", prepare)
        monkeypatch.setattr(
            "areal_rsi.coordination.preparation.asyncio.create_subprocess_exec", spawn
        )
        arguments = {
            "adapter": "kernelbench",
            "target_model": "fixed-task-model",
            "target_base_url": "https://target.example/v1",
            "settings_json": json.dumps(
                {"repository": str(tmp_path), "dataset_config": str(selections)}
            ),
        }
        configured = await prepare_example(c, app.repository, arguments, "setup")
        assert prepared_calls == [(tmp_path, tasks, "a" * 40)]
        assert configured.execution_env == {
            "RSI_TARGET_MODEL": "fixed-task-model",
            "RSI_TARGET_BASE_URL": "https://target.example/v1",
        }
        again = await prepare_example(c, app.repository, arguments, "setup")
        assert again.execution_env == configured.execution_env
        assert len(prepared_calls) == 1

    asyncio.run(scenario())
