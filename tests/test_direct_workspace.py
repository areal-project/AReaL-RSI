"""Requested Git repositories use linked worktrees across agent handoff."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from runtime_fakes import ActionRuntime, CallbackRuntime

from areal_rsi.api.interfaces import ToolCall, TurnResult
from areal_rsi.api.research import ResearchMessage, TaskBrief
from areal_rsi.infra.coordination.config import ResearchConfig, git_common_directory
from areal_rsi.infra.coordination.intake import ResearchIntake
from areal_rsi.infra.coordination.policies.passk import PassKActions
from areal_rsi.infra.coordination.tools.brief import PASSK_HIDDEN_LIMITS
from areal_rsi.infra.research import ResearchSystem
from areal_rsi.infra.runtime.runtime_package import sandbox_command
from areal_rsi.infra.storage.files import encode


def init_repo(project: Path) -> None:
    project.mkdir()
    (project / "code.py").write_text("answer = 0\n")
    subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(project), "config", "user.name", "RSI Test"], check=True)
    subprocess.run(
        ["git", "-C", str(project), "config", "user.email", "rsi-test@local.test"],
        check=True,
    )
    subprocess.run(["git", "-C", str(project), "add", "code.py"], check=True)
    subprocess.run(["git", "-C", str(project), "commit", "-qm", "baseline"], check=True)


def test_intake_binds_the_requested_directory(tmp_path: Path) -> None:
    async def scenario() -> None:
        project = tmp_path / "project"
        init_repo(project)
        other = tmp_path / "other"
        other.mkdir()
        seed = tmp_path / "seed"
        seed.mkdir()
        (seed / "old.py").write_text("old = True\n")
        app = ResearchSystem(
            tmp_path / "data",
            config=ResearchConfig(TaskBrief("Fix project", "Fix it"), seed_dir=str(seed)),
            runtime_factory=lambda _: ActionRuntime(),
        )
        try:
            await app.create({"run_id": "path-intake", "conversation": False})
            c = app.coordinator("path-intake")
            assert c.state.seed_candidate_id
            c.user_message(
                f"在 {project} 修改代码", key="explicit-path", recipient="interaction"
            )
            actions = PassKActions(c)

            async def start(_: str | None) -> None:
                raise AssertionError("set_execution must not start research")

            ResearchIntake(actions, repository=tmp_path, start=start)
            handler = actions.handler("interaction")
            first = await handler(ToolCall(
                "set_execution", {"execution_json": json.dumps({"workspace_dir": str(other)})},
                "first-path",
            ))
            assert "error" in first.output
            assert c.state.workspace_dir is None
            assert c.state.seed_candidate_id is not None
            subprocess.run(["git", "-C", str(other), "init", "-q"], check=True)
            no_head = await handler(ToolCall(
                "set_execution", {"execution_json": json.dumps({"workspace_dir": str(other)})},
                "no-head",
            ))
            assert "基线提交" in no_head.output["error"]
            accepted = await handler(ToolCall(
                "set_execution", {"execution_json": json.dumps({"workspace_dir": str(project)})},
                "user-path",
            ))
            assert "error" not in accepted.output
            assert accepted.output["workspace_dir"] == str(project)
            assert c.state.workspace_dir == str(project)
            assert actions.workspace("interaction") != project
            assert c.state.seed_candidate_id is None
        finally:
            await app.close()

    asyncio.run(scenario())


def test_one_researcher_and_interaction_share_the_selected_git_directory(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        project = tmp_path / "project"
        init_repo(project)
        app = ResearchSystem(
            tmp_path / "data",
            config=ResearchConfig(
                TaskBrief("Fix code", "Set answer to 42"),
                workspace_dir=str(project), model_settings_pinned=True,
            ),
            runtime_factory=lambda _: ActionRuntime(),
        )
        try:
            await app.create({"run_id": "direct", "conversation": False})
            await app.start("direct")
            async with asyncio.timeout(5):
                while not app.coordinator("direct").state.passk_ready:
                    await asyncio.sleep(.01)
            active = app.active["direct"]
            c = active.coordinator
            researcher = c.store.run().research_agents[0].agent_id
            assert (await app.detail("direct"))["workspace_dir"] == str(project)
            worktree = active.actions.workspace(researcher)
            assert worktree != project
            assert worktree.is_dir()
            assert active.actions.workspace("interaction") == worktree
            (worktree / "code.py").write_text("answer = 42\n")
            assert (project / "code.py").read_text() == "answer = 0\n"
            await app.stop("direct")
            assert (active.actions.workspace("interaction") / "code.py").read_text() == (
                "answer = 42\n"
            )
            subprocess.run(["git", "-C", str(worktree), "add", "code.py"], check=True)
            subprocess.run(["git", "-C", str(worktree), "commit", "-qm", "fix answer"], check=True)
            assert subprocess.check_output(
                ["git", "-C", str(worktree), "log", "-1", "--format=%s"], text=True,
            ).strip() == "fix answer"
            assert (project / "code.py").read_text() == "answer = 0\n"
            assert subprocess.check_output(
                ["git", "-C", str(project), "show", f"rsi/direct/{researcher}:code.py"],
                text=True,
            ) == "answer = 42\n"
            saved = await c.store.snapshot(
                worktree, agent_id=researcher, description="answer 42",
            )
            manifest = await c.store.content.read_json(saved.content)
            assert isinstance(manifest, dict)
            files = manifest.get("files")
            assert isinstance(files, dict)
            assert ".git" not in files
            (worktree / "code.py").write_text("answer = 43\n")
            newer = await c.store.snapshot(
                worktree, agent_id=researcher, description="answer 43",
                parent_candidate_id=saved.candidate_id,
            )
            active.actions.mark_workspace_candidate(researcher, newer.candidate_id)
            await active.actions.checkout_candidate(researcher, saved, "restore-answer-42")
            assert (worktree / "code.py").read_text() == "answer = 42\n"
            assert subprocess.check_output(
                ["git", "-C", str(worktree), "status", "--short"], text=True,
            ) == ""
            assert not (
                tmp_path / "data/runs/direct/agents" / researcher / "workspace/code.py"
            ).exists()
            if sys.platform == "darwin":
                session = tmp_path / "sandbox-session"
                session.mkdir()
                sandbox_command(
                    session, worktree, (),
                    writable_paths=(git_common_directory(project),),
                )
                profile = (session / "sandbox.sb").read_text()
                assert f'(allow file-write* (subpath "{worktree}"))' in profile
                assert (f'(allow file-write* (subpath "{git_common_directory(project)}"))'
                        in profile)
        finally:
            await app.close()

    asyncio.run(scenario())


def test_prompt_path_flows_from_intake_to_research_and_later_chat(tmp_path: Path) -> None:
    async def scenario() -> None:
        project = tmp_path / "project"
        init_repo(project)
        interaction_turns = 0
        worktree: Path | None = None

        async def respond(session: Path, delivery: dict[str, Any]) -> TurnResult:
            nonlocal interaction_turns, worktree
            actor = delivery["recovery_state"]["agent_id"]
            handler = delivery["handler"]
            message_id = delivery["message_id"]
            if actor == "interaction":
                interaction_turns += 1
                if interaction_turns == 1:
                    selected = await handler(ToolCall(
                        "set_execution",
                        {"execution_json": json.dumps({"workspace_dir": str(project)})},
                        "bind-user-directory",
                    ))
                    assert "error" not in selected.output
                    brief = encode(app.coordinator("prompt-path").store.brief())
                    for name in PASSK_HIDDEN_LIMITS:
                        brief["limits"].pop(name)
                    brief.update(
                        objective="Fix code in the named directory",
                        instructions="Set answer to 42 in the named directory",
                        acceptance=["code.py contains answer = 42"],
                    )
                    saved = await handler(ToolCall("update_brief", brief, "brief"))
                    assert "error" not in saved.output
                    handoff = await handler(ToolCall(
                        "handoff_research",
                        {"confirmation_message_id": message_id, "summary": "Fix code",
                         "execution_required": False},
                        "handoff",
                    ))
                    assert "error" not in handoff.output
                    return TurnResult(session.name, message_id, "", handoff.reason, {})
                assert delivery["workspace"] == worktree
                assert worktree is not None
                assert (worktree / "code.py").read_text() == "answer = 42\n"
                subprocess.run(["git", "-C", str(worktree), "add", "code.py"], check=True)
                subprocess.run(
                    ["git", "-C", str(worktree), "commit", "-qm", "fix answer"], check=True
                )
                return TurnResult(session.name, message_id, "本地提交完成", "completed", {})
            worktree = delivery["workspace"]
            assert worktree != project
            (worktree / "code.py").write_text("answer = 42\n")
            result = await handler(ToolCall(
                "update_goal",
                {"status": "completed", "summary": "Fixed code.py", "current_step": "done",
                 "next_steps": [], "acceptance": ["code.py contains answer = 42"],
                 "evidence": ["read back code.py"], "blocker": "", "candidate_id": ""},
                "goal-completed",
            ))
            assert "error" not in result.output
            return TurnResult(session.name, message_id, "Fixed code.py", result.reason, {})

        app = ResearchSystem(
            tmp_path / "data", runtime_factory=lambda _: CallbackRuntime(respond)
        )
        try:
            await app.create({
                "run_id": "prompt-path", "conversation": True,
                "message": f"在 {project} 修改 code.py，把 answer 改成 42，然后继续和我对话。",
            })
            async with asyncio.timeout(10):
                while (await app.detail("prompt-path"))["status"] != "succeeded":
                    await asyncio.sleep(.01)
            c = app.coordinator("prompt-path")
            assert c.state.workspace_dir == str(project)
            assert c.actor("interaction").session_id != "interaction"
            await app.close()
            app = ResearchSystem(
                tmp_path / "data", runtime_factory=lambda _: CallbackRuntime(respond)
            )
            c = app.coordinator("prompt-path")
            receipt = await app.send_message(
                "prompt-path", "把刚才的修改提交到本地", recipient="interaction"
            )
            async with asyncio.timeout(10):
                while not any(
                    message.sender == "interaction"
                    and message.reply_to == receipt["message_id"]
                    for message in c.store.records("messages", ResearchMessage)
                ):
                    await asyncio.sleep(.01)
            assert interaction_turns == 2
            assert worktree is not None
            assert subprocess.check_output(
                ["git", "-C", str(worktree), "log", "-1", "--format=%s"], text=True,
            ).strip() == "fix answer"
            assert (project / "code.py").read_text() == "answer = 0\n"
            assert c.state.status == "succeeded"
        finally:
            await app.close()

    asyncio.run(scenario())
