from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml  # type: ignore[import-untyped]

import areal_rsi.modules.dsh_config as dsh_config
import areal_rsi.modules.dsh_profile as dsh_profile
import areal_rsi.ui.dsh as dsh
from areal_rsi.modules.dsh import DSH_VERSION

PLUGIN_PATCH = "- insert:\n  - id: dsh-tui\n    name: '@deepseek-harness-tui/dsh-tui'\n"


def installed_profile(root: Path) -> tuple[Path, Path, Path]:
    home = root.resolve() / "dsh-home"
    profile = home / "profiles" / dsh_profile.PROFILE
    source = profile / "node_modules" / dsh_profile.TUI_PACKAGE / "cordis.patch.yml"
    source.parent.mkdir(parents=True)
    source.write_text(PLUGIN_PATCH)
    manifest = profile / "package.json"
    manifest.write_text(
        json.dumps(
            {
                "name": "fixture-profile",
                "dependencies": {dsh_profile.TUI_PACKAGE: dsh_profile.TUI_VERSION},
                "dsh": {
                    "profile": {
                        "bundles": ["@deepseek-ai/dsh-base", dsh_profile.TUI_PACKAGE],
                        "patchReload": "live",
                    }
                },
            }
        )
    )
    preset = (
        root
        / "dsh-cli/node_modules/.pnpm"
        / (
            f"@deepseek-ai+dsh-agent-presets@{DSH_VERSION}_fixture/node_modules/"
            "@deepseek-ai/dsh-agent-presets/presets/standard/agent.cordis.yml"
        )
    )
    preset.parent.mkdir(parents=True)
    preset.write_text(
        "- id: persona\n  name: '@deepseek-ai/dsh-persona'\n"
        "  config:\n    text: original\n"
        "- id: tool-bash\n  name: '@deepseek-ai/dsh-tool-bash'\n"
    )
    return home, manifest, source


def test_installed_profile_preparation_is_idempotent_without_pnpm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, manifest, source = installed_profile(tmp_path)
    binary = tmp_path / "fake-dsh"
    monkeypatch.setattr(dsh_profile, "ensure_cli", lambda _: binary)
    installer = Mock(side_effect=AssertionError("an installed plugin must not be reinstalled"))
    monkeypatch.setattr("areal_rsi.modules.dsh_profile.subprocess.run", installer)
    monkeypatch.setattr("areal_rsi.modules.dsh_profile.shutil.which", lambda _: None)

    assert dsh_profile.ensure_profile(tmp_path) == (binary, home)
    prepared = manifest.read_bytes(), source.read_bytes()
    assert dsh_profile.ensure_profile(tmp_path) == (binary, home)
    assert (manifest.read_bytes(), source.read_bytes()) == prepared
    configured = json.loads(manifest.read_text())
    assert configured["dsh"]["profile"]["bundles"] == [
        "@deepseek-ai/dsh-base",
        dsh_profile.TUI_PACKAGE,
    ]
    assert configured["dsh"]["profile"]["patchReload"] == "live"
    assert configured["dependencies"][dsh_profile.TUI_PACKAGE] == dsh_profile.TUI_VERSION
    assert source.read_text() == PLUGIN_PATCH
    installer.assert_not_called()


def test_missing_pnpm_fails_before_attempting_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dsh_profile, "ensure_cli", lambda _: tmp_path / "fake-dsh")
    monkeypatch.setattr("areal_rsi.modules.dsh_profile.shutil.which", lambda _: None)
    installer = Mock(side_effect=AssertionError("installation must not run without pnpm"))
    monkeypatch.setattr("areal_rsi.modules.dsh_profile.subprocess.run", installer)
    with pytest.raises(RuntimeError, match=r"首次安装.*pnpm"):
        dsh_profile.ensure_profile(tmp_path)
    installer.assert_not_called()
    assert not (tmp_path / "dsh-home").exists()


def test_existing_preset_refreshes_persona_and_preserves_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, _, _ = installed_profile(tmp_path)
    output = dsh_profile.ensure_rsi_preset(tmp_path, home)
    tree = yaml.safe_load(output.read_text())
    assert tree[0]["config"]["text"] == dsh_config.PERSONA
    assert tree[1] == {"id": "tool-bash", "name": "@deepseek-ai/dsh-tool-bash"}
    tree[1]["config"] = {"timeout": 42}
    output.write_text(
        yaml.safe_dump(tree) + "- id: custom-tool\n  config:\n    cwd: !!js process.cwd()\n"
    )
    monkeypatch.setattr(dsh_profile, "PERSONA", "Updated Interaction Agent persona\n")
    dsh_profile.ensure_rsi_preset(tmp_path, home)
    text = output.read_text()
    refreshed = yaml.safe_load(text.replace("!!js", "!!str"))
    assert refreshed[0]["config"]["text"] == "Updated Interaction Agent persona\n"
    assert refreshed[1] == tree[1]
    assert refreshed[2] == {"id": "custom-tool", "config": {"cwd": "process.cwd()"}}
    assert "!!js" in text
    modified = output.stat().st_mtime_ns
    dsh_profile.ensure_rsi_preset(tmp_path, home)
    assert output.read_text() == text
    assert output.stat().st_mtime_ns == modified


def test_responses_frontend_uses_native_provider_and_runtime_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RSI_TUI_MODEL_API", "responses")
    path = dsh_config.write_profile_patch(tmp_path, tmp_path, "gpt-5.6-luna")
    patch = yaml.safe_load(path.read_text().replace("!!js", "!!str"))
    frontend = next(row for row in patch if row.get("id") == "dsh-tui")
    provider = next(row for row in patch if row.get("id") == "llm-pi-ai")
    assert frontend["config"]["provider"] == "rsi-responses"
    route = provider["config"]["providers"]["rsi-responses"]
    assert route["api"] == "openai-responses"
    assert route["apiKeyEnv"] == "DEEPSEEK_API_KEY"
    node = shutil.which("node")
    assert node is not None
    result = subprocess.run(
        [node, "-e", "process.stdout.write(" + route["baseURL"] + ")"],
        env={"DEEPSEEK_BASE_URL": "http://127.0.0.1:12345"},
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    assert result.stdout == "http://127.0.0.1:12345/v1"


def test_native_launcher_keeps_dsh_session_and_new_backend_separate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "data"
    home = tmp_path / "dsh-home"
    monkeypatch.setenv("RSI_TEACHER_MODEL", "frontend-fixture")
    monkeypatch.setenv("RSI_TEACHER_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("RSI_TEACHER_API_KEY", "private-fixture")
    monkeypatch.setenv("RSI_AGENT_MODELS", "{}")
    monkeypatch.setattr(dsh, "ensure_profile", lambda _: (tmp_path / "dsh", home))
    backend = Mock(url="http://127.0.0.1:18799")
    ensure = Mock(return_value=(backend, {}))
    monkeypatch.setattr(dsh, "ensure_workbench", ensure)
    proxy = Mock(url="http://127.0.0.1:12345")
    monkeypatch.setattr(dsh, "GatewayProxy", Mock(return_value=proxy))
    child = Mock()
    child.wait.return_value = 0
    child.poll.return_value = 0
    popen = Mock(return_value=child)
    monkeypatch.setattr("areal_rsi.ui.dsh.subprocess.Popen", popen)
    assert dsh.launch_tui(root, port=18799, session="existing-dsh-session") == 0
    argv = popen.call_args.args[0]
    assert argv[-2:] == ["--resume", "existing-dsh-session"]
    assert "--profile" in argv and "rsi-chimney" in argv
    ensure.assert_called_once_with(18799, root)
    patch = Path(argv[argv.index("--patch") + 1]).read_text()
    assert "areal_rsi.ui.mcp" in patch and "18799" in patch
    assert "private-fixture" not in patch
    assert popen.call_args.kwargs["env"]["DSH_HOME"] == str(home)
    child.terminate.assert_not_called()
    proxy.close.assert_called_once()


def test_patch_forwards_only_runtime_credential_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "RSI_AGENT_MODELS", json.dumps({"interaction-agent": {"api_key_env": "FRONTEND_KEY"}})
    )
    monkeypatch.setenv("FRONTEND_KEY", "never-save-this-key")
    output = dsh_config.write_profile_patch(
        tmp_path, tmp_path, "fixture", port=18799, run_id="research_existing"
    )
    text = output.read_text()
    assert "never-save-this-key" not in text
    patch = yaml.safe_load(text.replace("!!js", "!!str"))
    mcp = next(
        item for row in patch for item in row.get("insert", []) if item.get("id") == "rsi-mcp"
    )["config"]
    assert "FRONTEND_KEY" in mcp["env"]
    assert "RSI_TARGET_API_KEY" in mcp["env"]
    assert "RSI_MODE" not in json.loads(
        mcp["env"].split("Object.fromEntries(", 1)[1].split(".flatMap", 1)[0]
    )
    assert mcp["args"][-2:] == ["--run", "research_existing"]
