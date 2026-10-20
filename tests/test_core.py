"""Configuration isolation and semantic experience updates."""

import os
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from areal_rsi.api.control import LessonDraft
from areal_rsi.infra.config import load_config, runtime_environment
from areal_rsi.infra.experience import ExperienceLibrary


def test_project_config_and_credentials_do_not_leak(tmp_path: Path) -> None:
    user = tmp_path / "config/areal-rsi"
    project = tmp_path / "project"
    user.mkdir(parents=True)
    project.mkdir()
    (user / ".env").write_text("RSI_API_KEY=user-key\n")
    (project / ".env").write_text("RSI_API_KEY=project-key\nRSI_MODEL=project-model\n")
    (project / "rsi.toml").write_text('[session]\nmodel = "file-model"\n')
    environment = {k: v for k, v in os.environ.items() if not k.startswith("RSI_")}
    with patch.dict(os.environ, {**environment, "XDG_CONFIG_HOME": str(user.parent)}, clear=True):
        first, second = load_config(project).session, load_config(tmp_path).session
        assert first.model == "project-model"
        assert runtime_environment(first)["RSI_API_KEY"] == "project-key"
        assert runtime_environment(second)["RSI_API_KEY"] == "user-key"
        assert "RSI_API_KEY" not in os.environ
        (project / "rsi.toml").write_text('[session]\napi_key = "invalid"\n')
        with pytest.raises(ValueError, match="Invalid AppConfig"):
            load_config(project)


def test_experience_updates_one_topic_and_rejects_stale_writes(tmp_path: Path) -> None:
    library = ExperienceLibrary(tmp_path)
    draft = LessonDraft("graph-validation", "Graph checks", "After edits", "Validate the batch.")
    first = library.publish(draft)
    path = Path(first.path)
    before = path.read_text()
    updated = library.publish(
        replace(draft, body="Validate rollback too."), previous_content=before
    )
    assert updated.path == first.path and path.name == "graph-validation.md"
    assert library.documents() == [updated] and list(tmp_path.iterdir()) == [path]
    with pytest.raises(ValueError, match="changed"):
        library.publish(draft, previous_content=before)
    assert "rollback" in path.read_text()
