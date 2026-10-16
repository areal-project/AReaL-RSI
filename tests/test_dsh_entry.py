from __future__ import annotations

import json
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, sentinel

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
    assert configured["dsh"]["profile"]["patchReload"] == "startup"
    assert configured["dependencies"][dsh_profile.TUI_PACKAGE] == dsh_profile.TUI_VERSION
    assert source.read_text() == PLUGIN_PATCH
    installer.assert_not_called()


def test_shared_tui_cache_does_not_move_goal_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "goal-data"
    cache = tmp_path / "local-cache"
    home, _, _ = installed_profile(cache)
    monkeypatch.setenv("RSI_DSH_ROOT", str(cache))
    monkeypatch.setenv("RSI_TEACHER_MODEL", "fixture-model")
    monkeypatch.setenv("RSI_TEACHER_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setattr(dsh_profile, "ensure_cli", lambda _: cache / "fake-dsh")
    assert dsh.launch_tui(data, setup_only=True) == 0
    assert (home / "profiles" / dsh_profile.PROFILE / "package.json").is_file()
    assert (data / "dsh-entry.patch.yml").is_file()
    assert not (data / "dsh-home").exists()


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


@pytest.mark.parametrize("resume", [None, "main-session-existing"])
def test_launch_uses_native_process_patches_and_absolute_mcp_root_without_saving_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resume: str | None
) -> None:
    root = tmp_path / "state"
    home, _, _ = installed_profile(root)
    workspace = tmp_path / "work"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    binary = tmp_path / "fake-dsh"
    monkeypatch.setattr(dsh_profile, "ensure_cli", lambda _: binary)
    installer = Mock(side_effect=AssertionError("plugin was already installed"))
    monkeypatch.setattr("areal_rsi.modules.dsh_profile.subprocess.run", installer)
    monkeypatch.setenv("RSI_TEACHER_BASE_URL", "https://gateway.example/api/")
    monkeypatch.setenv("RSI_TEACHER_API_KEY", "fixture-private-rsi-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-private-deepseek-key")
    monkeypatch.setenv("RSI_TEACHER_MODEL", "teacher-default-fixture-model")
    monkeypatch.setenv("RSI_TARGET_MODEL", "target-default-fixture-model")
    monkeypatch.setenv("INTERACTION_MODEL_KEY", "fixture-interaction-agent-key")
    monkeypatch.setenv(
        "RSI_AGENT_MODELS",
        json.dumps(
            {
                "interaction-agent": {
                    "model": "interaction-agent-fixture-model",
                    "base_url": "https://interaction-agent.example/v1",
                    "api_key_env": "INTERACTION_MODEL_KEY",
                }
            }
        ),
    )
    proxy = Mock(url="http://127.0.0.1:12345/v1")
    gateway = Mock(return_value=proxy)
    monkeypatch.setattr(dsh, "GatewayProxy", gateway)
    child = Mock()
    child.wait.return_value = 17
    child.poll.return_value = 17
    popen = Mock(return_value=child)
    monkeypatch.setattr("areal_rsi.ui.dsh.subprocess.Popen", popen)
    monkeypatch.setattr(
        "areal_rsi.ui.dsh.signal.getsignal", Mock(return_value=sentinel.old_handler)
    )
    set_signal = Mock()
    monkeypatch.setattr("areal_rsi.ui.dsh.signal.signal", set_signal)

    assert dsh.launch_tui(Path("../state"), resume=resume) == 17

    gateway.assert_called_once_with(
        "https://interaction-agent.example/v1", "fixture-interaction-agent-key"
    )
    command = [
        str(binary),
        "--profile",
        dsh_profile.PROFILE,
        "--patch",
        str(root / "dsh-entry.patch.yml"),
    ]
    if resume is not None:
        command += ["--resume", resume]
    popen.assert_called_once()
    assert popen.call_args.args == (command,)
    assert popen.call_args.kwargs["cwd"] == workspace
    environment = popen.call_args.kwargs["env"]
    assert environment["DSH_HOME"] == str(home)
    assert environment["DEEPSEEK_BASE_URL"] == proxy.url
    assert environment["DEEPSEEK_API_KEY"] == "local-proxy"
    assert environment["DSH_TELEMETRY_DISABLED"] == "1"
    patch = yaml.safe_load((root / "dsh-entry.patch.yml").read_text().replace("!!js", "!!str"))
    agent = next(row["config"] for row in patch if row.get("id") == "dsh-tui")
    assert agent["model"] == "interaction-agent-fixture-model"
    assert agent["preset"] == "rsi"
    assert agent["effort"] == "off"
    assert agent["cwd"] == str(workspace)
    assert not any(row.get("id") == "agent-loop" for row in patch)
    adapter = next(row["config"] for row in patch if row.get("id") == "llm-deepseek")
    assert adapter["models"] == [{"id": "interaction-agent-fixture-model"}]
    assert adapter["maxTokens"] == 8192
    assert adapter["thinking"] == "disabled"
    activity = next(row["config"] for row in patch if row.get("id") == "working-activity")
    assert activity == {
        "publish": False,
        "publishIntervalMs": 500,
        "narrate": False,
    }
    assert {row["id"] for row in patch if row.get("disabled")} == {
        "session-title-llm",
        "dsh-tui-auth",
    }
    mcp = next(
        entry for row in patch for entry in row.get("insert", []) if entry.get("id") == "rsi-mcp"
    )
    assert mcp["name"] == "@deepseek-ai/dsh-mcp-client"
    assert mcp["config"]["transport"] == "stdio"
    assert mcp["config"]["command"] == sys.executable
    assert mcp["config"]["args"] == ["-m", "areal_rsi", "_mcp", "--root", str(root)]
    assert mcp["config"]["cwd"] == str(workspace)
    # 不执行表达式；检查文件仅引用宿主变量，没有固化凭证值。
    assert "process.env[key]" in mcp["config"]["env"]
    assert "RSI_API_KEY" in mcp["config"]["env"]
    assert "RSI_RESEARCH_AGENT_COUNT" in mcp["config"]["env"]
    assert "DEEPSEEK_API_KEY" not in mcp["config"]["env"]
    assert mcp["config"]["failOnStartupError"] is True
    for path in root.rglob("*"):
        if path.is_file():
            assert "fixture-private-rsi-key" not in path.read_text()
            assert "fixture-private-deepseek-key" not in path.read_text()
            assert "fixture-interaction-agent-key" not in path.read_text()
    child.wait.assert_called_once_with()
    child.terminate.assert_not_called()
    proxy.close.assert_called_once_with()
    set_signal.assert_called_with(signal.SIGINT, sentinel.old_handler)
    installer.assert_not_called()


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


def test_setup_only_prepares_files_without_starting_gateway_or_tui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RSI_TEACHER_MODEL", "teacher-default-fixture-model")
    monkeypatch.setenv("RSI_TEACHER_BASE_URL", "http://127.0.0.1:1/v1")
    home, _, _ = installed_profile(tmp_path)
    monkeypatch.setattr(dsh_profile, "ensure_cli", lambda _: tmp_path / "fake-dsh")
    gateway = Mock(side_effect=AssertionError("setup must not start a gateway"))
    popen = Mock(side_effect=AssertionError("setup must not launch an interactive process"))
    installer = Mock(side_effect=AssertionError("plugin was already installed"))
    monkeypatch.setattr(dsh, "GatewayProxy", gateway)
    monkeypatch.setattr("areal_rsi.ui.dsh.subprocess.Popen", popen)
    monkeypatch.setattr("areal_rsi.modules.dsh_profile.subprocess.run", installer)

    assert dsh.launch_tui(tmp_path, setup_only=True) == 0
    assert (home / "profiles" / dsh_profile.PROFILE / "package.json").is_file()
    assert (tmp_path / "dsh-entry.patch.yml").is_file()
    gateway.assert_not_called()
    popen.assert_not_called()
    installer.assert_not_called()


def test_mcp_environment_is_forwarded_at_runtime_without_persisting_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("DSH environment expressions require Node.js")
    overrides = json.dumps({"research-agent": {"api_key_env": "MODEL_OVERRIDE_KEY"}})
    monkeypatch.setenv("RSI_AGENT_MODELS", overrides)
    path = dsh_config.write_profile_patch(tmp_path, tmp_path, "interaction-agent-fixture-model")
    patch = yaml.safe_load(path.read_text().replace("!!js", "!!str"))
    mcp = next(
        item for row in patch for item in row.get("insert", []) if item.get("id") == "rsi-mcp"
    )
    supplied = {
        "RSI_AGENT_MODELS": overrides,
        "MODEL_OVERRIDE_KEY": "override-fixture-key",
        "RSI_TEACHER_MODEL": "teacher-default-fixture-model",
        "RSI_TEACHER_API_KEY": "teacher-fixture-credential",
        "RSI_TARGET_MODEL": "target-default-fixture-model",
        "RSI_TARGET_API_KEY": "target-fixture-credential",
        "RSI_RESEARCH_AGENT_COUNT": "3",
        "UNRELATED_API_KEY": "unrelated-credential",
    }
    result = subprocess.run(
        [node, "-e", "process.stdout.write(JSON.stringify(" + mcp["config"]["env"] + "))"],
        env=supplied,
        text=True,
        capture_output=True,
        check=True,
        timeout=5,
    )
    assert json.loads(result.stdout) == {
        key: value
        for key, value in supplied.items()
        if key.startswith("RSI_") or key == "MODEL_OVERRIDE_KEY"
    }
    assert "fixture-credential" not in path.read_text()
    assert "unrelated-credential" not in path.read_text()
    assert "override-fixture-key" not in path.read_text()


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
