"""Local login shares only the auth file; native sessions and API routes stay isolated."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_runtime_tool_grants import request

from areal_rsi.api.research import TaskBrief
from areal_rsi.infra.config import local_codex_auth
from areal_rsi.infra.coordination.config import ResearchConfig, validate_model_settings
from areal_rsi.infra.runtime.runtime_package import sandbox_command
from areal_rsi.infra.runtime.runtime_worker import compile_config


def test_local_login_validation_and_native_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "local"))
    monkeypatch.setenv("RSI_API_KEY", "must-not-be-used")
    config = ResearchConfig(TaskBrief("test", "test"), backend="codex", auth_mode="codex_login",
                            model_settings_pinned=True)
    with pytest.raises(ValueError, match="Codex"):
        config.runtime()
    auth = tmp_path / "local/auth.json"
    auth.parent.mkdir()
    auth.write_text(json.dumps({"tokens": {"access_token": "fixture-only"}}))
    assert local_codex_auth() == auth
    assert config.api_key() == ""
    config.runtime()
    settings = validate_model_settings({**config.model_settings(), "base_url": "https://untrusted.example"})
    assert settings["base_url"] == "https://api.openai.com"
    with pytest.raises(ValueError, match="Codex"):
        validate_model_settings({**settings, "backend": "standard"})
    value = request(tmp_path, "codex-cli")
    value.update(mcp=[], codex_login=True)
    compiled, _ = compile_config(value)
    serialized = json.dumps(compiled)
    assert "apiKeyEnv" not in serialized and "baseUrl" not in serialized
    native = (tmp_path / "codex-home/config.toml").read_text()
    assert 'model_provider = "rsi-local"' in native
    assert 'cli_auth_credentials_store = "file"' in native


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS auth-file sandbox")
def test_auth_refresh_writes_shared_file_but_cannot_read_other_codex_data(tmp_path: Path) -> None:
    source = tmp_path / "local/auth.json"
    source.parent.mkdir()
    source.write_text('{"tokens": {"access_token": "old"}}')
    other = source.parent / "history.jsonl"
    other.write_text("private history")
    directory, workspace = tmp_path / "runtime", tmp_path / "workspace"
    directory.mkdir()
    workspace.mkdir()
    link = directory / "auth.json"
    link.symlink_to(source)
    sandbox_command(directory, workspace, (source,), codex_auth_file=source)
    script = (
        "from pathlib import Path\n"
        f"Path({str(link)!r}).write_text('refreshed')\n"
        "try:\n"
        f" Path({str(other)!r}).read_text()\n"
        "except PermissionError:\n print('ISOLATED')\n"
    )
    result = subprocess.run(
        ["sandbox-exec", "-f", str(directory / "sandbox.sb"), sys.executable, "-I", "-c", script],
        text=True, capture_output=True, check=True,
    )
    assert source.read_text() == "refreshed" and link.is_symlink()
    assert result.stdout.strip() == "ISOLATED"
