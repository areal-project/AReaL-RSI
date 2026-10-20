"""Install the latest CLI in an isolated environment, then launch the plugin."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPOSITORY = "https://github.com/areal-project/AReaL-RSI.git"


def install(directory: Path) -> Path:
    """Publish a complete installation; never modify an environment already in use."""
    current = directory / "current"
    repository = os.environ.get("RSI_CLI_REPOSITORY", REPOSITORY)
    ref = os.environ.get("RSI_CLI_REF", "main")
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--exit-code", repository, f"refs/heads/{ref}"],
            check=True, capture_output=True, text=True, timeout=15,
            stdin=subprocess.DEVNULL,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        revision = result.stdout.split()[0]
        if not re.fullmatch(r"[0-9a-f]{40,64}", revision):
            raise ValueError("Invalid CLI source revision")
        environment = directory / revision
        cli = environment / "bin" / "areal-rsi"
        if not (environment / ".ready").exists() or not cli.is_file():
            if not shutil.which("uv"):
                raise RuntimeError("Install uv first: https://docs.astral.sh/uv/getting-started/")
            print("AReaL-RSI: installing the latest CLI…", file=sys.stderr)
            shutil.rmtree(environment, ignore_errors=True)
            subprocess.run(
                ["uv", "venv", "--no-config", "--python", "3.12", str(environment)],
                check=True, stdin=subprocess.DEVNULL, stdout=sys.stderr,
            )
            subprocess.run(
                ["uv", "pip", "install", "--no-config", "--python",
                 str(environment / "bin" / "python"), f"git+{repository}@{revision}"],
                check=True, stdin=subprocess.DEVNULL, stdout=sys.stderr,
            )
            subprocess.run(
                [str(cli), "mcp", "--help"], check=True,
                stdin=subprocess.DEVNULL, stdout=sys.stderr,
            )
            (environment / ".ready").touch()
        pending = directory / "current.next"
        pending.unlink(missing_ok=True)
        pending.symlink_to(cli)
        pending.replace(current)
        return cli
    except (OSError, ValueError, IndexError, RuntimeError, subprocess.SubprocessError) as exc:
        if not current.is_file():
            raise RuntimeError(f"CLI installation failed: {exc}") from exc
        print(f"AReaL-RSI: update unavailable; using the installed CLI ({exc}).", file=sys.stderr)
        return current.resolve()


def service_cli(cli: Path, arguments: list[str]) -> Path:
    """Reconnect through the running service's own installation until it is stopped."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--data-root", "--root")
    options, _ = parser.parse_known_args(arguments)
    root = ["--data-root", options.data_root] if options.data_root else []
    result = subprocess.run(
        [str(cli), "service", "status", *root], capture_output=True, text=True, timeout=35,
        stdin=subprocess.DEVNULL,
    )
    if result.returncode:
        if "RSI service is not running" in result.stderr:
            return cli
        raise RuntimeError(f"Cannot inspect the running RSI service: {result.stderr.strip()}")
    # service_info is deliberately available before the client-version check.
    executable = json.loads(result.stdout).get("executable")
    if not isinstance(executable, str):
        raise RuntimeError("Stop the older RSI service before loading the updated plugin")
    running = Path(executable).with_name("areal-rsi")
    if not running.is_file():
        raise RuntimeError("The running RSI service's CLI is missing; stop that service first")
    if running.resolve() != cli.resolve():
        print("AReaL-RSI: CLI updated; this connection keeps the running service's version. "
              "Stop the service and reload the plugin to switch versions.", file=sys.stderr)
    return running


def main() -> None:
    arguments = sys.argv[1:] or ["mcp"]
    # Explicit opt-out for local development; never replace an editable installation.
    override = os.environ.get("RSI_CLI_PATH")
    if override:
        cli = Path(override).expanduser().absolute()
    else:
        directory = Path(os.environ.get(
            "RSI_CLI_HOME", "~/.local/share/areal-rsi/cli",
        )).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (directory / "install.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            cli = install(directory)
    if arguments == ["--setup"]:
        print(f"AReaL-RSI CLI ready: {cli}")
        return
    if arguments[0] == "mcp":
        cli = service_cli(cli, arguments)
    os.execv(str(cli), [str(cli), *arguments])


if __name__ == "__main__":
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"AReaL-RSI: {error}", file=sys.stderr)
        sys.exit(1)
