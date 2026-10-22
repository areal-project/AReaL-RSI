"""Container-only entry: loopback proxy, isolation probe, and native Codex CLI."""

from __future__ import annotations

import argparse
import json
import os
import select
import socket
import socketserver
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModelSettings:
    model: str
    effort: str
    base_url: str | None = None
    context_window: int | None = None
    auto_compact_limit: int | None = None


def relay(left: socket.socket, right: socket.socket) -> None:
    try:
        while True:
            readable, _, _ = select.select([left, right], [], [], 300)
            if not readable:
                return
            for source in readable:
                data = source.recv(65536)
                if not data:
                    return
                (right if source is left else left).sendall(data)
    except OSError:
        return


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as remote:
            remote.connect("/proxy/relay.sock")
            relay(self.request, remote)


def probe() -> None:
    """Fail closed before any model request if filesystem/network isolation is missing."""
    checks = []
    for path in json.loads(Path("/control/denied.json").read_text()):
        target = Path(path)
        try:
            blocked = not target.exists()
        except PermissionError:
            blocked = True
        checks.append({"path": path, "blocked": blocked})
        if not blocked:
            raise RuntimeError(f"Hidden path is accessible: {path}")
    if os.getuid() == 0:
        raise RuntimeError("Agent must run as an unprivileged UID")
    status = Path("/proc/self/status").read_text()
    if "CapEff:\t0000000000000000" not in status:
        raise RuntimeError("Agent has effective capabilities")
    with socket.socket() as connection:
        connection.settimeout(2)
        try:
            connection.connect(("1.1.1.1", 443))
        except OSError:
            pass
        else:
            raise RuntimeError("Agent has direct network access")
    with socket.create_connection(("127.0.0.1", 1081), timeout=10) as connection:
        connection.sendall(b"CONNECT github.com:443 HTTP/1.1\r\nHost: github.com:443\r\n\r\n")
        if b"403" not in connection.recv(1024).split(b"\r\n", 1)[0]:
            raise RuntimeError("Proxy permits benchmark-source egress")
    # Compile/run an independently written self-test using the exact Agent mount/UID.
    with Path("/tmp/smoke.sv").open("w") as file:
        file.write('module smoke; initial begin $display("AGENT_EDA_OK"); $finish; end endmodule\n')
    subprocess.run(
        ["iverilog", "-g2012", "-s", "smoke", "-o", "/tmp/smoke", "/tmp/smoke.sv"], check=True
    )
    subprocess.run(["vvp", "/tmp/smoke"], check=True)
    print("ISOLATION_OK " + json.dumps({"uid": os.getuid(), "denied": checks}), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--effort", default="high")
    args = parser.parse_args()
    os.environ["HOME"] = "/agent-home"
    settings_file = Path("/control/model.json")
    settings = (
        ModelSettings(**json.loads(settings_file.read_text()))
        if settings_file.exists()
        else ModelSettings(args.model, args.effort)
    )
    with Server(("127.0.0.1", 1081), Handler) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        probe()
        if args.probe_only:
            return
        command = [
            "/usr/local/bin/codex",
            "-a",
            "never",
            "exec",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--ephemeral",
            "--json",
            "--color",
            "never",
            "--sandbox",
            "danger-full-access",
            "--model",
            settings.model,
            "--config",
            f'model_reasoning_effort="{settings.effort}"',
            "--config",
            'web_search="disabled"',
            "--config",
            "project_doc_max_bytes=0",
            "--config",
            'cli_auth_credentials_store="file"',
            "--disable",
            "memories",
            "--disable",
            "multi_agent",
            "--cd",
            "/workspace",
            "-",
        ]
        if settings.base_url is not None:
            for setting in (
                'model_provider="benchmark"',
                'model_providers.benchmark.name="Benchmark inference"',
                "model_providers.benchmark.base_url=" + json.dumps(settings.base_url),
                'model_providers.benchmark.wire_api="responses"',
                "model_providers.benchmark.requires_openai_auth=false",
                "model_providers.benchmark.supports_websockets=false",
                'model_providers.benchmark.capabilities.remote_compaction="unsupported"',
            ):
                command.extend(["--config", setting])
        if settings.context_window is not None:
            command.extend(["--config", f"model_context_window={settings.context_window}"])
        if settings.auto_compact_limit is not None:
            command.extend(
                ["--config", f"model_auto_compact_token_limit={settings.auto_compact_limit}"]
            )
        for feature in (
            "apps",
            "plugins",
            "remote_plugin",
            "hooks",
            "browser_use",
            "computer_use",
            "in_app_browser",
            "shell_snapshot",
        ):
            command.extend(["--disable", feature])
        with Path("/control/instructions.txt").open() as instructions:
            result = subprocess.run(command, stdin=instructions)
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
