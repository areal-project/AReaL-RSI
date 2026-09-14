"""Long-lived team mechanics, independent of any model's research ability."""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import suppress
from functools import partial
from pathlib import Path
from typing import Any

from areal_rsi.api.base import AgentState, RecordRef
from areal_rsi.api.research import (
    CandidateSnapshot,
    ResearchLimits,
    ResearchReport,
    TaskBrief,
    VerificationContract,
    VerifiedResult,
)
from areal_rsi.base.storage import now
from areal_rsi.coordination.actions import ResearchActions
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.coordination.engine import ResearchEngine, ToolSet
from areal_rsi.coordination.jobs import ResearchExecution
from areal_rsi.coordination.store import ResearchStore
from areal_rsi.execution import ApprovedCommand, JobRecord
from areal_rsi.modules.responses import ResponsesConfig, ResponsesRuntime, ToolCall


def test_72_nodes_keep_sessions_and_bounded_state_across_worker_restarts(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(
            tmp_path,
            TaskBrief(
                "长期研究测试", "每轮汇报后等待新任务", ResearchLimits(max_agents=3, max_nodes=100)
            ),
        )
        c.start()
        agents = [
            c.spawn(key=str(i), direction=f"方向 {i}", instruction="检查现有依据")["agent_id"]
            for i in range(3)
        ]
        calls: list[dict[str, Any]] = []

        class Transport:
            async def request(self, config: Any, payload: Any, sink: Any) -> dict[str, Any]:
                calls.append(payload)
                state = json.JSONDecoder().raw_decode(
                    payload["instructions"].split("当前 run 的系统状态：\n", 1)[1]
                )[0]
                agent_id = state["agent_id"]
                output: list[dict[str, Any]]
                if agent_id == "rsi":
                    output = [
                        {
                            "type": "function_call",
                            "name": "wait",
                            "call_id": str(len(calls)),
                            "arguments": json.dumps({"reason": "等待下一条研究消息"}),
                        }
                    ]
                else:
                    output = [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": (
                                        "本轮检查完成，发现输入条件尚不足以作出性能判断。"
                                        "下一轮应补充实测。"
                                    ),
                                }
                            ],
                        }
                    ]
                return {"status": "completed", "output": output, "usage": {"total_tokens": 10}}

        def tools_for(actions: ResearchActions, agent_id: str) -> ToolSet:
            return ToolSet(actions.definitions(agent_id), actions.handler(agent_id))

        sessions: dict[str, str | None] = {}
        for round_index in range(24):
            # Each new worker loads durable records, rather than borrowing live objects.
            c = Coordinator(ResearchStore(tmp_path, c.store.run_id))
            actions = ResearchActions(c)
            if round_index:
                for agent_id in agents:
                    c.assign(
                        agent_id, key=f"{round_index}:{agent_id}", instruction="继续检查下一组证据"
                    )
            engine = ResearchEngine(
                c,
                ResponsesRuntime(
                    ResponsesConfig(base_url="https://example.test", api_key="test"), Transport()
                ),
                partial(tools_for, actions),
            )
            await engine.run_until_idle(timeout=5)
            count = len(calls)
            await engine.run_until_idle(timeout=2)
            assert len(calls) == count  # Dormant agents spend no calls while waiting.
            for agent_id in agents:
                actor = c.actor(agent_id)
                assert actor.state == AgentState.WAITING and actor.wait_reason == "rsi_reply"
                sessions.setdefault(agent_id, actor.session_id)
                assert actor.session_id == sessions[agent_id]
                assert len(actor.iterations) == round_index + 1
                assert all(n.report_ref is not None for n in actor.iterations)
            # Archive verbose observations as in the problematic real run.
            saved = await actions.handler("rsi")(
                ToolCall(
                    "save_evidence",
                    {
                        "title": f"第 {round_index} 轮观察",
                        "summary": "完整观察及依据。" * 300,
                        "references": [],
                        "limitations": "不代表已完成验证",
                    },
                    f"evidence:{round_index}",
                )
            )
            await actions.handler("rsi")(
                ToolCall(
                    "share_evidence",
                    {
                        "evidence_id": saved.output["evidence_id"],
                        "limitations": "保留原始局限",
                    },
                    f"share:{round_index}",
                )
            )
        state = (await actions.handler("rsi")(ToolCall("get_state", {}, "state"))).output
        assert len(json.dumps(state, ensure_ascii=False)) < 12000
        assert len(state["evidence"]) <= 5 and state["evidence_has_more"]
        assert len(c.store.records("reports", ResearchReport)) == 72
        assert not (c.store.root / "candidates").exists()
        seen: list[str] = []
        offset = 0
        while offset is not None:
            result = (
                await actions.handler("rsi")(
                    ToolCall(
                        "list_records", {"category": "reports", "offset": offset}, f"page:{offset}"
                    )
                )
            ).output
            seen.extend(item["uri"] for item in result["records"])
            offset = result["next_offset"]
        assert len(set(seen)) == 72
        for uri in (seen[0], seen[-1]):
            assert actions.read_record("rsi", uri)["message"].startswith("本轮检查完成")
        for agent_id in agents:
            session = (
                c.store.paths.agent(agent_id)
                / "sessions"
                / str(sessions[agent_id])
                / "session.json"
            )
            history = json.loads(session.read_text())
            assert len(history["turns"]) == 24
        assert not c.state.get("turn_failures")

    asyncio.run(scenario())


def test_old_report_is_readable_without_rewriting_archive(tmp_path: Path) -> None:
    c = Coordinator.create(tmp_path, TaskBrief("兼容性", "读取历史记录"))
    old = {
        "report_id": "old",
        "iteration_id": "node",
        "assignment_id": "task",
        "task_status": "完成检查",
        "work": "读取执行轨迹",
        "problems": "改动未被加载",
        "results": "没有测到新策略",
        "interpretation": "尚不能判断策略收益",
        "next_steps": "修正加载方式",
        "shared_findings": [],
        "references": [],
        "created_at": now(),
    }
    ref = c.store.put("reports", "old", old)
    assert "尚不能判断策略收益" in c.store.get("reports", "old", ResearchReport).message
    assert c.store.records("reports", ResearchReport)[0].report_id == "old"
    assert c.store.public_record(ref) == old


def test_delegated_researcher_can_wait_and_read_only_published_verifier_feedback(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        c = Coordinator.create(
            tmp_path,
            TaskBrief("共享作业", "检查公开反馈", verification=VerificationContract("accuracy")),
        )
        c.start()
        ra = c.spawn(key="ra", direction="分析反馈", instruction="分析 RSI 的评测结果")["agent_id"]
        c.begin_turn(ra)
        actions = ResearchActions(c)
        candidate = await c.store.snapshot(
            actions.workspace("rsi"), agent_id="rsi", description="测试输入"
        )
        script = (
            "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); "
            "(p/'public').mkdir(); (p/'private.txt').write_text('PRIVATE'); "
            "(p/'public'/'feedback.txt').write_text('PUBLIC: 7/8 passed'); time.sleep(.1)"
        )

        def verify(job: JobRecord, snapshot: CandidateSnapshot, phase: str) -> VerifiedResult:
            return VerifiedResult(
                job.job_id,
                snapshot.candidate_id,
                c.store.contract_digest(),
                now(),
                "succeeded",
                0.875,
                True,
            )

        execution = ResearchExecution(
            actions,
            verifier=ApprovedCommand(
                "verify",
                (sys.executable, "-c", script, "{output}"),
                ("output",),
                public_output_subdir="public",
            ),
            read_verification=verify,
        )
        try:
            job = await actions.handler("rsi")(
                ToolCall(
                    "verify_candidate",
                    {
                        "candidate_id": candidate.candidate_id,
                        "phase": "search",
                    },
                    "verify",
                )
            )
            job_id = job.output["job_id"]
            handler = actions.handler(ra)
            assert (await handler(ToolCall("job_status", {"job_id": job_id}, "status"))).output[
                "agent_id"
            ] == "rsi"
            assert (await handler(ToolCall("wait_for_job", {"job_id": job_id}, "wait"))).stop
            c.end_turn(ra)
            for _ in range(200):
                if not await execution.poll():
                    break
                await asyncio.sleep(0.02)
            assert ra in c.pending()
            feedback = await handler(
                ToolCall(
                    "read_job_output_file",
                    {"job_id": job_id, "path": "feedback.txt", "offset": 0},
                    "feedback",
                )
            )
            assert feedback.output["content"] == "PUBLIC: 7/8 passed"
            for name, args in (
                ("read_job_log", {"job_id": job_id}),
                ("read_job_output_file", {"job_id": job_id, "path": "../private.txt", "offset": 0}),
            ):
                denied = await handler(ToolCall(name, args, name))
                assert "error" in denied.output and "PRIVATE" not in str(denied.output)
            c.begin_turn(ra)
            report = c.report(ra, key="report", fields={"message": "公开反馈为 7/8 通过。"})
            stored = c.store.get("reports", report["report_id"], ResearchReport)
            assert RecordRef(feedback.output["uri"]) in stored.references
            assert any("/verifications/" in ref.uri for ref in stored.references)
            assert execution.manager.get(job_id).state == "succeeded"
        finally:
            execution.close(cancel=True)

    asyncio.run(scenario())


def test_hidden_tools_still_resume_pending_calls_and_preserve_chinese(tmp_path: Path) -> None:
    from areal_rsi.coordination.actions import definition
    from areal_rsi.modules.responses import ToolResult

    async def scenario() -> None:
        payloads = []
        effects = set()

        class Transport:
            async def request(self, config: Any, payload: Any, sink: Any) -> dict[str, Any]:
                payloads.append(payload)
                output = (
                    [
                        {
                            "type": "function_call",
                            "name": "legacy",
                            "call_id": "saved_call",
                            "arguments": "{}",
                        }
                    ]
                    if len(payloads) == 1
                    else [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "完成"}],
                        }
                    ]
                )
                return {"status": "completed", "output": output}

        async def handle(call: ToolCall) -> ToolResult:
            effects.add(call.call_id)
            if not call.recovered:
                raise asyncio.CancelledError
            return ToolResult({"结果": "已恢复操作，无需重新执行"})

        config = ResponsesConfig(base_url="https://example.test", api_key="test")
        runtime = ResponsesRuntime(config, Transport())
        tools = [definition("legacy", "旧工具")]
        with suppress(asyncio.CancelledError):
            await runtime.turn(
                tmp_path,
                system_prompt="继续原任务",
                user_message="开始",
                message_id="m",
                tools=tools,
                handler=handle,
            )
        await runtime.turn(
            tmp_path,
            system_prompt="继续原任务",
            user_message=None,
            message_id="m",
            tools=tools,
            advertised_tools=[],
            handler=handle,
        )
        assert effects == {"saved_call"}
        assert not payloads[-1].get("tools")
        result = next(
            item["output"]
            for item in payloads[-1]["input"]
            if item.get("type") == "function_call_output"
        )
        assert "已恢复操作" in result and "\\u" not in result

    asyncio.run(scenario())


def test_finish_validates_before_stopping_team(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(
            tmp_path,
            TaskBrief(
                "完成目标", "保留验证边界", verification=VerificationContract("score", target=0.8)
            ),
        )
        c.start()
        ra = c.spawn(key="a", direction="改进", instruction="推进任务")["agent_id"]
        actions = ResearchActions(c)
        candidate = await c.store.snapshot(actions.workspace(ra), agent_id=ra, description="产物")
        arguments = {"summary": "目标达成", "candidate_id": candidate.candidate_id}
        denied = await actions.handler("rsi")(ToolCall("finish", arguments, "premature"))
        assert "error" in denied.output
        assert c.actor(ra).state != AgentState.STOPPED
        c.store.record_verification(
            VerifiedResult(
                "check",
                candidate.candidate_id,
                c.store.contract_digest(),
                now(),
                "succeeded",
                0.8,
                True,
            )
        )
        completed = await actions.handler("rsi")(ToolCall("finish", arguments, "finish"))
        assert completed.stop and c.state["status"] == "succeeded"
        assert c.actor(ra).state == AgentState.STOPPED

    asyncio.run(scenario())


def test_late_supervisor_update_continues_before_automatic_report(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("中途补充", "连续研究"))
        c.start()
        ra = c.spawn(key="a", direction="检查", instruction="检查输入")["agent_id"]
        actions = ResearchActions(c)
        turns: list[dict[str, Any]] = []

        class Transport:
            async def request(self, config: Any, payload: Any, sink: Any) -> dict[str, Any]:
                state = json.JSONDecoder().raw_decode(
                    payload["instructions"].split("当前 run 的系统状态：\n", 1)[1]
                )[0]
                output: list[dict[str, Any]]
                if state["agent_id"] == "rsi":
                    output = [
                        {
                            "type": "function_call",
                            "name": "wait",
                            "call_id": f"wait:{len(turns)}",
                            "arguments": '{"reason":"等待报告"}',
                        }
                    ]
                else:
                    turns.append(payload)
                    if len(turns) == 1:
                        c.message_researcher(ra, "也检查空输入", key="update")
                    else:
                        assert "也检查空输入" in json.dumps(payload["input"], ensure_ascii=False)
                    output = [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "已检查输入"
                                    if len(turns) == 1
                                    else "输入和空输入都检查完毕",
                                }
                            ],
                        }
                    ]
                return {"status": "completed", "output": output}

        engine = ResearchEngine(
            c,
            ResponsesRuntime(
                ResponsesConfig(base_url="https://example.test", api_key="test"), Transport()
            ),
            lambda aid: ToolSet(actions.definitions(aid), actions.handler(aid)),
        )
        await engine.run_until_idle(timeout=3)
        reports = c.store.records("reports", ResearchReport)
        assert len(reports) == 1 and reports[0].message == "输入和空输入都检查完毕"
        assert len(c.actor(ra).iterations) == 1
        assert len(turns) == 2 and not c.state.get("turn_failures")

    asyncio.run(scenario())


def test_verifying_identical_seed_content_records_baseline(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(
            tmp_path,
            TaskBrief("基线内容", "同一输入", verification=VerificationContract("score")),
            simulated=True,
        )
        c.start()
        actions = ResearchActions(c)
        workspace = actions.workspace("rsi")
        (workspace / "input.txt").write_text("same input")
        seed = await c.store.snapshot(workspace, agent_id="operator", description="起点")
        clone = await c.store.snapshot(workspace, agent_id="rsi", description="再次保存起点")
        assert seed.candidate_id != clone.candidate_id and seed.content == clone.content
        c.state["baseline_candidate_id"] = seed.candidate_id
        c.save()

        def verify(job: JobRecord, snapshot: CandidateSnapshot, phase: str) -> VerifiedResult:
            return VerifiedResult(
                job.job_id,
                snapshot.candidate_id,
                c.store.contract_digest(),
                now(),
                "succeeded",
                0.7,
                True,
                simulated=True,
            )

        execution = ResearchExecution(
            actions,
            verifier=ApprovedCommand("verify", ("test-verifier",)),
            read_verification=verify,
            simulated=True,
            mock_duration=0.01,
        )
        try:
            await actions.handler("rsi")(
                ToolCall(
                    "verify_candidate",
                    {"candidate_id": clone.candidate_id, "phase": "search"},
                    "verify",
                )
            )
            for _ in range(100):
                if not await execution.poll():
                    break
                await asyncio.sleep(0.01)
            baseline = c.store.run().baseline_result_ref
            assert baseline is not None
            assert c.store.public_record(baseline)["candidate_id"] == clone.candidate_id
            assert c.store.public_record(baseline)["score"] == 0.7
        finally:
            execution.close(cancel=True)

    asyncio.run(scenario())
