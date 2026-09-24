"""v0.3 selects complete attempts, publishes evidence-backed bundles and reuses them."""

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import pytest
from areal_agent_runtime.contracts.events import EventType
from test_passk import ScriptedRuntime, configuration
from test_runtime_storage import event, initial

from areal_rsi.base.storage import write_json
from areal_rsi.coordination.collaboration import build_plan
from areal_rsi.coordination.config import ResearchConfig
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.coordination.engine import ToolSet
from areal_rsi.coordination.profile_evolution import freeze_library, initialize, review_profiles
from areal_rsi.modules.agent_runtime import ToolCall, TurnResult
from areal_rsi.modules.runtime_storage import JournalStateStore
from areal_rsi.profile_library import load_library, publish, revise
from areal_rsi.research_app import ResearchApp

MODULES = (
    "leader_guidance",
    "problem_sharing",
    "researcher_profiles",
    "profile_resources",
    "profile_evolution",
)


def config(root: Path, count: int = 2) -> ResearchConfig:
    result = configuration(count)
    result.collaboration_modules = MODULES
    result.profile_library_dir = str(root / "shared-library")
    result.metadata["auto_resume"] = True
    return result


def update(evidence: str, identifier: str = "kernelbench/execution") -> dict[str, Any]:
    return {
        "profile_id": identifier,
        "reason": "轨迹显示先核对计数器权限可避免重复失败",
        "reminder": "先确认诊断工具可用，再选择测量方法。",
        "evidence": [evidence],
        "files": [
            {"path": "AGENTS.md", "content": "保持独立探索，优先用证据诊断 GPU 等待。"},
            {"path": "knowledge/core.md", "content": "入口存在不代表计数器权限可用。"},
            {
                "path": "skills/check-tools/SKILL.md",
                "content": "---\nname: check-tools\ndescription: 诊断前检查工具。\n---\n"
                "先检查版本，再做有范围的采集；计数器权限不足时用已有计时工具。",
            },
        ],
    }


def test_selection_reaches_prompt_workspace_and_recovery(tmp_path: Path) -> None:
    class Capture(ScriptedRuntime):
        async def turn(self, session_dir: Path, **kwargs: Any) -> TurnResult:
            assert "GPU 等待" not in kwargs["system_prompt"]  # No future revisions.
            assert "Kernel 性能诊断" in kwargs["system_prompt"]
            assert "knowledge/core.md" in kwargs["system_prompt"]
            assert kwargs["recovery_state"]["researcher_profile"]["id"] == "kernelbench/execution"
            return TurnResult("session", "turn", "继续", "completed", {})

    async def scenario() -> None:
        cfg = config(tmp_path)
        freeze_library(cfg, tmp_path)
        c = Coordinator.create(tmp_path / "data", cfg.brief)
        c.state["mode"] = "passk"
        c.start()
        plan = build_plan("passk", MODULES)
        actions = plan.actions(c)
        initialize(actions, cfg)
        handler = actions.handler("rsi")
        invalid = await handler(
            ToolCall("start_researchers", {"profile_ids": ["missing"], "reason": "test"}, "invalid")
        )
        assert "error" in invalid.output and not c.store.run().research_agents
        chosen = ["kernelbench/execution"] * 2
        result = await handler(
            ToolCall(
                "start_researchers",
                {"profile_ids": chosen, "reason": "瓶颈不明，独立诊断"},
                "select",
            )
        )
        assert "error" not in result.output
        assert c.state["profile_selection"]["profile_ids"] == chosen
        immutable = copy.deepcopy(c.state["researcher_profiles"])
        changed = await handler(
            ToolCall(
                "start_researchers", {"profile_ids": ["general"] * 2, "reason": "更换"}, "change"
            )
        )
        assert "error" in changed.output
        restored = Coordinator(c.store)
        recovered_actions = plan.actions(restored)
        initialize(recovered_actions, cfg)
        assert restored.state["researcher_profiles"] == immutable
        engine = plan.engine(
            c,
            Capture(),
            lambda a: ToolSet(actions.definitions(a), actions.handler(a)),
            research_prompt=plan.research_prompt,
            rsi_prompt=plan.rsi_prompt,
        )
        for actor in c.store.run().research_agents:
            assert (
                actions.workspace(actor.agent_id)
                / "researcher/skills/kernel-profile/scripts/profile_kernel.py"
            ).is_file()
            await engine._drive(actor.agent_id)

    asyncio.run(scenario())


def test_automatic_closeout_publishes_and_next_run_loads_revision(tmp_path: Path) -> None:
    class Runtime(ScriptedRuntime):
        reviews = 0

        async def turn(self, session_dir: Path, **kwargs: Any) -> TurnResult:
            handler = kwargs["handler"]
            if kwargs["recovery_state"].get("phase") == "profile_review":
                self.reviews += 1
                context = json.loads(kwargs["user_message"])
                assert context["selection"]["profile_ids"] == ["kernelbench/execution"]
                agent = next(iter(context["sessions"]))
                trace = await handler(
                    ToolCall(
                        "read_researcher_trace",
                        {
                            "agent_id": agent,
                            "session_id": context["sessions"][agent][0],
                            "offset": 0,
                        },
                        "read-trace",
                    )
                )
                assert "counter permission denied" in trace.output["content"]
                evidence = trace.output["uri"]
                bad = await handler(
                    ToolCall(
                        "finish_profile_review",
                        {
                            "summary": "证据检查",
                            "updates": [update("run://another/materials/proof")],
                        },
                        "bad-evidence",
                    )
                )
                assert "error" in bad.output
                read = await handler(ToolCall("read_record", {"uri": evidence}, "read-proof"))
                assert "counter permission" in read.output["content"]
                result = await handler(
                    ToolCall(
                        "finish_profile_review",
                        {
                            "summary": "身份明确诊断偏好；知识补充权限条件；技能增加检查步骤。",
                            "updates": [update(evidence)],
                        },
                        "publish",
                    )
                )
                assert result.stop and result.output["status"] == "completed"
            elif kwargs["recovery_state"]["agent_id"] == "rsi":
                result = await handler(
                    ToolCall(
                        "start_researchers",
                        {"profile_ids": ["kernelbench/execution"], "reason": "先诊断硬件执行"},
                        "select",
                    )
                )
                assert "error" not in result.output
            else:
                # Finish exploration through the real app serve/closeout path.
                journal = JournalStateStore(session_dir / "runtime/data/state")
                try:
                    await journal.create_run(initial())
                    observed = event(0, EventType.MESSAGE_COMPLETED).model_copy(
                        update={
                            "data": {
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "counter permission denied; timing works",
                                    }
                                ],
                            }
                        }
                    )
                    await journal.commit_event(
                        observed, initial().model_copy(update={"last_sequence": 0}), outbox_sinks=()
                    )
                finally:
                    journal.close()
                next(iter(app.active.values())).coordinator.stop("test exploration finished")
            return TurnResult(session_dir.name, "turn", "done", "completed", {})

    async def scenario() -> None:
        created = await app.create({})
        c = app.coordinator(created["run_id"])
        original = copy.deepcopy(app.config(c).metadata["profile_library"])
        await app.start(c)
        active = app.active[c.store.run_id]
        await asyncio.wait_for(active.task, 10)
        c = active.coordinator
        assert c.state["profile_evolution"]["status"] == "completed"
        assert runtime.reviews == 1
        assert app.config(c).metadata["profile_library"] == original
        await review_profiles(c, app.config(c), runtime, active.actions)
        assert runtime.reviews == 1
        next_run = await app.create({})
        newer = app.config(app.coordinator(next_run["run_id"]))
        profile = newer.metadata["profile_library"]["kernelbench/execution"]
        assert "GPU 等待" in profile["prompt"]
        assert "skills/check-tools/SKILL.md" in profile["resources"]
        assert "入口存在" in profile["resources"]["knowledge/core.md"]
        assert (c.store.root / "profile-evolution/review.json").is_file()

    runtime = Runtime()
    app = ResearchApp(
        tmp_path / "data", config=config(tmp_path, 1), runtime_factory=lambda _: runtime
    )
    asyncio.run(scenario())


def test_concurrent_reviews_and_publication_replay(tmp_path: Path) -> None:
    library = load_library(tmp_path)
    first = revise(library, [update("materials/proof")])
    result = publish(tmp_path, "first", library, first, {"summary": "first"})
    assert result["status"] == "completed"
    assert publish(tmp_path, "first", library, first, {"summary": "ignored replay"}) == result
    second = copy.deepcopy(first)
    second["kernelbench/execution"]["reminder"] = "stale concurrent edit"
    conflict = publish(tmp_path, "second", library, second, {"summary": "second"})
    assert conflict["status"] == "conflict"
    assert load_library(tmp_path)["kernelbench/execution"] == first["kernelbench/execution"]
    other = revise(library, [update("materials/proof", "general")])
    assert publish(tmp_path, "other", library, other, {})["status"] == "completed"


@pytest.mark.parametrize(
    "path",
    [
        "../escape.md",
        "/tmp/escape.md",
        "knowledge/../../escape.md",
        "config.json",
        "skills/tool.sh",
    ],
)
def test_revision_rejects_non_asset_paths(tmp_path: Path, path: str) -> None:
    change = update("materials/proof")
    change["files"] = [{"path": path, "content": "bad"}]
    with pytest.raises(ValueError):
        revise(load_library(tmp_path), [change])


@pytest.mark.parametrize("mode", ["noop", "missing_submission", "interrupted_commit", "user_stop"])
def test_review_completion_and_interrupted_commit(tmp_path: Path, mode: str) -> None:
    class Runtime(ScriptedRuntime):
        calls_count = 0

        async def turn(self, session_dir: Path, **kwargs: Any) -> TurnResult:
            self.calls_count += 1
            if mode == "noop":
                await kwargs["handler"](
                    ToolCall(
                        "finish_profile_review",
                        {"summary": "身份、知识和技能均暂无可靠改进", "updates": []},
                        "noop",
                    )
                )
            return TurnResult("review", "turn", "done", "completed", {})

    async def scenario() -> None:
        cfg = config(tmp_path, 1)
        freeze_library(cfg, tmp_path)
        c = Coordinator.create(tmp_path / "data", cfg.brief)
        c.state["mode"] = "passk"
        c.start()
        actions = build_plan("passk", MODULES).actions(c)
        initialize(actions, cfg)
        await actions.handler("rsi")(
            ToolCall(
                "start_researchers",
                {"profile_ids": ["general"], "reason": "open exploration"},
                "select",
            )
        )
        c.stop("done")
        if mode == "interrupted_commit":
            write_json(
                c.store.root / "profile-evolution/proposal.json",
                {"summary": "身份、知识和技能无需更新", "updates": []},
            )
        if mode == "user_stop":
            c.state["user_stopped"] = True
        runtime = Runtime()
        await review_profiles(c, cfg, runtime, actions)
        assert c.state["profile_evolution"]["status"] == {
            "missing_submission": "failed",
            "user_stop": "skipped",
        }.get(mode, "completed")
        assert runtime.calls_count == (0 if mode in {"interrupted_commit", "user_stop"} else 1)

    asyncio.run(scenario())


def test_workbench_switches_to_v03_before_start(tmp_path: Path) -> None:
    async def scenario() -> None:
        cfg = configuration(2)
        cfg.collaboration_modules = MODULES[:3]
        cfg.researcher_profile_ids = ("general", "general")
        app = ResearchApp(tmp_path, config=cfg)
        created = await app.create({})
        c = app.coordinator(created["run_id"])
        await app.set_collaboration(c, {"mode": "passk", "modules": list(MODULES)})
        changed = app.config(c)
        assert not changed.researcher_profile_ids
        assert "researcher_profiles" not in changed.metadata
        assert "kernelbench/execution" in changed.metadata["profile_library"]
        initialize(build_plan("passk", MODULES).actions(c), changed)

    asyncio.run(scenario())


def test_cli_waits_for_review_after_final_verification() -> None:
    from areal_rsi.ui.cli import _idle_reason

    assert (
        _idle_reason(
            {
                "status": "succeeded",
                "mode": "passk",
                "worker_alive": True,
                "finalization": {"status": "completed"},
                "profile_evolution": {"status": "running"},
                "agents": [{"state": "stopped"}],
            }
        )
        is None
    )
