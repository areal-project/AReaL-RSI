from pathlib import Path
from typing import Any

import pytest

from areal_rsi.modules.runtime_worker import compile_config


def request(tmp_path: Path, kind: str = "dsh") -> dict[str, Any]:
    return {
        "directory": str(tmp_path),
        "workspace": str(tmp_path),
        "runtime": {"kind": kind},
        "base_url": "http://localhost:1",
        "model": "fixture",
        "max_tokens": 100,
        "tools": [],
        "mcp": [{"serverName": "evidence-files"}],
        "skills": [],
        "prompts": [],
        "agent_id": "research",
        "model_version_id": "fixture",
    }


@pytest.mark.parametrize("kind", ["dsh", "anthropic", "fake"])
def test_mcp_only_does_not_enable_workspace_tools(tmp_path: Path, kind: str) -> None:
    config, _ = compile_config(request(tmp_path, kind))
    assert config["workspaceTools"]["enabled"] is False
    assert config["toolBindings"] == []


@pytest.mark.parametrize("kind", ["codex-cli", "hermes"])
def test_native_mcp_cannot_implicitly_authorize_shell(tmp_path: Path, kind: str) -> None:
    with pytest.raises(ValueError, match="explicit bash@v1"):
        compile_config(request(tmp_path, kind))


def test_editor_grant_does_not_authorize_shell(tmp_path: Path) -> None:
    value = request(tmp_path)
    value["tools"] = ["editor@v1"]
    config, _ = compile_config(value)
    tools = config["toolBindings"][0]["toolNames"]
    assert "workspace.edit" in tools and "workspace.write" in tools
    assert "workspace.run" not in tools
    value["tools"].append("bash@v1")
    config, _ = compile_config(value)
    assert "workspace.run" in config["toolBindings"][0]["toolNames"]
