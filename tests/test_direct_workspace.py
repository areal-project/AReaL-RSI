"""The executing Leader owns an isolated Git worktree and immutable candidates."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from runtime_fakes import ActionRuntime

from areal_rsi.api.research import TaskBrief
from areal_rsi.infra.coordination.config import ResearchConfig
from areal_rsi.infra.research import ResearchSystem


def init_repo(project: Path) -> None:
    project.mkdir()
    (project / "code.py").write_text("answer = 0\n")
    for arguments in (
        ("init", "-q"),
        ("config", "user.name", "RSI Test"),
        ("config", "user.email", "rsi-test@local.test"),
        ("add", "code.py"),
        ("commit", "-qm", "baseline"),
    ):
        subprocess.run(["git", "-C", str(project), *arguments], check=True)


def test_leader_worktree_preserves_user_checkout_and_snapshot_restore(tmp_path: Path) -> None:
    async def scenario() -> None:
        project = tmp_path / "project"
        init_repo(project)
        (project / "code.py").write_text("uncommitted user work\n")
        app = ResearchSystem(tmp_path / "data", runtime_factory=lambda _: ActionRuntime())
        try:
            run_id = await app.create(
                ResearchConfig(
                    TaskBrief("Fix code", "Set answer to 42"),
                    workspace_dir=str(project),
                    model_settings_pinned=True,
                )
            )
            await app.start(run_id)
            actions = app.active[run_id].actions
            worktree = actions.workspace("rsi")
            assert worktree != project and (worktree / "code.py").read_text() == "answer = 0\n"
            assert actions.workspace("interaction") != worktree
            (worktree / "code.py").write_text("answer = 42\n")
            c = app.coordinator(run_id)
            saved = await c.store.snapshot(worktree, agent_id="rsi", description="answer 42")
            manifest = await c.store.content.read_json(saved.content)
            assert isinstance(manifest, dict) and isinstance(manifest["files"], dict)
            assert ".git" not in manifest["files"]
            (worktree / "code.py").write_text("answer = 43\n")
            newer = await c.store.snapshot(worktree, agent_id="rsi", description="answer 43")
            actions.mark_workspace_candidate("rsi", newer.candidate_id)
            await actions.checkout_candidate("rsi", saved, "restore")
            assert (worktree / "code.py").read_text() == "answer = 42\n"
            assert (worktree / ".git").is_file()
            assert (project / "code.py").read_text() == "uncommitted user work\n"
            await app.stop(run_id)
        finally:
            await app.close()

    asyncio.run(scenario())
