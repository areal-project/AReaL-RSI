"""RSI sessions backed by the public ARealAgentRuntime Python package.

Each conversation owns an embedded composition in a separate Python process.
macOS uses sandbox-exec. Linux can use Bubblewrap, or trusted deployments can
explicitly disable isolation.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from areal_rsi.api.base import AgentSpec, Trajectory, TrajectoryRef
from areal_rsi.base.storage import FileContentStore, FileTrajectoryStore, new_id, now, safe_name


class RuntimeSettings:
    """Trusted deployment settings, never supplied by a candidate or model."""

    def __init__(self, value: dict[str, Any]):
        if not isinstance(value, dict):
            raise ValueError("Runtime config must be an object")

        def check_secrets(item: Any) -> None:
            if isinstance(item, dict):
                for key, child in item.items():
                    normalized = key.lower().replace("_", "").replace("-", "")
                    if (
                        normalized
                        in {
                            "apikey",
                            "authtoken",
                            "authorization",
                            "xapikey",
                            "openaiapikey",
                            "anthropicapikey",
                        }
                        and child
                    ):
                        raise ValueError(
                            "Use RSI model credentials or authTokenEnv; "
                            "literal Runtime secrets are not supported"
                        )
                    check_secrets(child)
            elif isinstance(item, list):
                for child in item:
                    check_secrets(child)

        check_secrets(value)
        if set(value) - {"default", "roles", "readable_paths"}:
            raise ValueError("Runtime config only accepts default, roles, readable_paths")
        self.isolation = os.environ.get("RSI_RUNTIME_ISOLATION", "sandbox")
        validate_isolation(self.isolation)
        self.default = value.get("default", {"kind": "dsh", "config": {}})
        self.roles = value.get("roles", {})
        if set(self.roles) - {"rsi-agent", "research-agent", "task-agent"}:
            raise ValueError("Unknown Runtime role")
        self.readable_paths = tuple(
            Path(p).expanduser().resolve() for p in value.get("readable_paths", [])
        )
        provenance = Path(__file__).parents[1] / "resources/runtime/provenance.json"
        self.identity = hashlib.sha256(
            json.dumps(
                {
                    "runtime": value,
                    **({"isolation": self.isolation} if self.isolation != "sandbox" else {}),
                    "state_backend": "rsi-runtime-journal-v1",
                    "event_sinks": [],
                },
                sort_keys=True,
            ).encode()
            + provenance.read_bytes()
        ).hexdigest()

    @classmethod
    def from_environment(cls) -> RuntimeSettings:
        from areal_rsi.experiment_config import configured_research_harness

        path = os.environ.get("RSI_RUNTIME_CONFIG")
        value = json.loads(Path(path).expanduser().read_text()) if path else {}
        harness = configured_research_harness()
        if harness:
            if not isinstance(value, dict):
                raise ValueError("Runtime config must be an object")
            roles = value.setdefault("roles", {})
            if not isinstance(roles, dict):
                raise ValueError("Runtime roles must be an object")
            for role in ("rsi-agent", "research-agent"):
                selected = roles.get(role, value.get("default", {}))
                # Preserve backend-specific settings only when the selected kind matches.
                if isinstance(selected, dict) and selected.get("kind") == harness:
                    selected = copy.deepcopy(selected)
                else:
                    selected = {"kind": harness, "config": {}}
                config = selected.setdefault("config", {})
                if not isinstance(config, dict):
                    raise ValueError("Runtime backend config must be an object")
                if harness == "codex-cli":
                    config.setdefault("modelProtocol", "responses")
                roles[role] = selected
        return cls(value)

    def select(self, role: str) -> dict[str, Any]:
        role = "research-agent" if role.startswith("research-agent-") else role
        result: dict[str, Any] = copy.deepcopy(self.roles.get(role, self.default))
        from areal_agent_runtime import RuntimeBootstrapConfig

        result = RuntimeBootstrapConfig.model_validate(result).model_dump(
            mode="json", by_alias=True, exclude_unset=True
        )
        for name in (
            "codexBin",
            "cliPath",
            "python",
            "hermesRoot",
            "caFile",
            "clientCertFile",
            "clientKeyFile",
        ):
            path = result.get("config", {}).get(name)
            if isinstance(path, str) and path.startswith("~"):
                result["config"][name] = str(Path(path).expanduser())
        scanner = result.get("config", {}).get("env", {}).get("TIRITH_BIN")
        if isinstance(scanner, str) and scanner.startswith("~"):
            result["config"]["env"]["TIRITH_BIN"] = str(Path(scanner).expanduser())
        return result


def validate_isolation(mode: str) -> None:
    if mode not in {"sandbox", "linux-bwrap", "linux-unrestricted"}:
        raise ValueError("RSI_RUNTIME_ISOLATION must be sandbox, linux-bwrap or linux-unrestricted")
    if mode.startswith("linux-") and sys.platform != "linux":
        raise RuntimeError(f"{mode} mode is only supported on Linux")


def _bwrap_parent_dirs(paths: set[Path]) -> list[str]:
    """Create mount destinations without exposing their host-side parents."""
    parents: set[Path] = set()
    for path in paths:
        current = path.parent
        while current != current.parent:
            parents.add(current)
            current = current.parent
    result: list[str] = []
    for path in sorted(parents, key=lambda item: (len(item.parts), str(item))):
        result.extend(["--dir", str(path)])
    return result


def _linux_bwrap_command(
    directory: Path,
    workspace: Path,
    readable: tuple[Path, ...],
    readonly_mounts: tuple[tuple[Path, Path], ...],
    worker: list[str],
) -> list[str]:
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        raise RuntimeError("RSI_RUNTIME_ISOLATION=linux-bwrap requires Bubblewrap (bwrap) in PATH")
    writable = {directory.resolve(), workspace.resolve()}
    workspace_alias = Path("/workspace")
    aliased_readonly_mounts = tuple(
        (source, workspace_alias / destination.relative_to(workspace.resolve()))
        for source, destination in readonly_mounts
    )
    readonly = {path.resolve() for path in readable if path.exists()}
    # Runtime binaries and libraries are host installation inputs, never task data.
    for path in (
        Path("/usr"),
        Path("/usr/local"),
        Path("/lib"),
        Path("/lib64"),
        Path(sys.prefix),
        Path(sys.base_prefix),
        Path(sys.executable).absolute().parent,
    ):
        if path.exists() and not path.is_symlink():
            readonly.add(path.absolute())
    node_binary = shutil.which("node")
    if node_binary:
        resolved_node = Path(node_binary).resolve()
        # /usr/local/bin/node is commonly a symlink into an installation under
        # /opt. Mount the installation itself so the symlink remains usable.
        if resolved_node.exists() and len(resolved_node.parents) >= 2:
            readonly.add(resolved_node.parent.parent)
    system_files = tuple(
        path
        for path in (
            Path("/etc/ssl"),
            Path("/etc/pki"),
            Path("/etc/resolv.conf"),
            Path("/etc/hosts"),
            Path("/etc/nsswitch.conf"),
            Path("/etc/passwd"),
            Path("/etc/group"),
            Path("/etc/localtime"),
        )
        if path.exists()
    )
    executable_mounts: list[tuple[Path, Path]] = []
    executable = Path(sys.executable).absolute()
    seen: set[Path] = set()
    while executable.is_symlink() and executable not in seen:
        seen.add(executable)
        target = Path(os.readlink(executable))
        if not target.is_absolute():
            target = executable.parent / target
        if target.is_absolute() and len(target.parents) >= 2:
            alias_root = target.parent.parent
            executable_mounts.append((alias_root.resolve(), alias_root))
        executable = target
    mounts = (
        writable
        | readonly
        | set(system_files)
        | {destination for _, destination in readonly_mounts}
        | {destination for _, destination in aliased_readonly_mounts}
        | {destination for _, destination in executable_mounts}
        | {Path("/proc"), Path("/run"), workspace_alias}
    )
    command = [
        bwrap,
        "--unshare-user",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-cgroup",
        "--die-with-parent",
        "--new-session",
        "--dev",
        "/dev",
        "--dir",
        "/workspace",
        "--dir",
        "/proc",
        "--dir",
        "/proc/self",
        "--ro-bind",
        "/proc/self",
        "/proc/self",
        "--dir",
        "/run",
        "--tmpfs",
        "/run",
        *_bwrap_parent_dirs(mounts),
    ]
    for source in sorted(writable):
        command.extend(["--bind", str(source), str(source)])
    command.extend(["--bind", str(workspace.resolve()), str(workspace_alias)])
    for source in sorted(readonly):
        command.extend(["--ro-bind", str(source), str(source)])
    for source, destination in readonly_mounts:
        command.extend(["--ro-bind", str(source), str(destination)])
    for source, destination in aliased_readonly_mounts:
        command.extend(["--ro-bind", str(source), str(destination)])
    for source, destination in executable_mounts:
        command.extend(["--ro-bind", str(source), str(destination)])
    for path in system_files:
        command.extend(["--ro-bind", str(path), str(path)])
    for link in (Path("/bin"), Path("/sbin"), Path("/lib"), Path("/lib64")):
        if link.is_symlink():
            command.extend(["--symlink", os.readlink(link), str(link)])
    return [*command, "--chdir", str(workspace_alias), "--", *worker]


def _process_descendants(pid: int) -> list[int]:
    """Return Linux descendants, direct children first, for wrapper-aware shutdown."""
    found: list[int] = []
    pending = [pid]
    while pending:
        parent = pending.pop()
        children = Path(f"/proc/{parent}/task/{parent}/children")
        try:
            current = [int(value) for value in children.read_text().split()]
        except (FileNotFoundError, PermissionError, ValueError):
            continue
        pending.extend(current)
        found.extend(current)
    return found


def sandbox_command(
    directory: Path,
    workspace: Path,
    readable: tuple[Path, ...],
    executable_aliases: tuple[Path, ...] = (),
    readonly_mounts: tuple[tuple[Path, Path], ...] = (),
    *,
    isolation: str = "sandbox",
) -> list[str]:
    validate_isolation(isolation)
    worker = [
        sys.executable,
        "-I",
        "-c",
        "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));"
        "runpy.run_module('areal_rsi.modules.runtime_worker',run_name='__main__')",
        str(directory / "worker-code"),
    ]
    if isolation == "linux-unrestricted":
        if readonly_mounts:
            raise RuntimeError("Read-only workspace mounts require linux-bwrap isolation")
        return worker
    if isolation == "linux-bwrap":
        return _linux_bwrap_command(directory, workspace, readable, readonly_mounts, worker)
    if readonly_mounts:
        raise RuntimeError("Read-only workspace mounts require linux-bwrap isolation")
    if sys.platform != "darwin":
        raise RuntimeError(
            "Agent sandbox requires macOS sandbox-exec; on Linux set "
            "RSI_RUNTIME_ISOLATION=linux-bwrap, or explicitly use linux-unrestricted "
            "for a trusted host"
        )
    # Installed packages are readable, experiment data is not. No source checkout is granted.
    roots = {
        directory.resolve(),
        workspace.resolve(),
        Path(sys.prefix).resolve(),
        Path(sys.base_prefix).resolve(),
        Path(sys.executable).resolve().parent,
        *readable,
    }
    profile = [
        "(version 1)",
        "(allow default)",
        '(deny file-read* (subpath "/Users") (subpath "/private/tmp") '
        '(subpath "/private/var/folders"))',
        "(deny file-write*)",
    ]
    for path in sorted(roots):
        profile.append(f"(allow file-read* (subpath {json.dumps(str(path))}))")
    # The installed RSI package includes benchmark/verifier code and hidden
    # fixture answers. The worker uses a minimal materialized namespace instead.
    profile.append(
        f"(deny file-read* (subpath {json.dumps(str(Path(__file__).resolve().parents[1]))}))"
    )
    for executable in (Path(sys.executable), *executable_aliases):
        seen: set[Path] = set()
        while executable.is_symlink() and executable not in seen:
            seen.add(executable)
            alias = Path(os.readlink(executable))
            if not alias.is_absolute():
                alias = executable.parent / alias
            profile.append(
                f"(allow file-read-metadata (subpath {json.dumps(str(alias.parent.parent))}))"
            )
            executable = alias
    for parent in sorted({p for root in roots for p in root.parents}):
        profile.append(f"(allow file-read-metadata (literal {json.dumps(str(parent))}))")
    for path in (directory.resolve(), workspace.resolve()):
        profile.append(f"(allow file-write* (subpath {json.dumps(str(path))}))")
    profile.append(
        '(allow file-read* file-write* (literal "/dev/null") (literal "/dev/tty") '
        '(literal "/dev/ptmx") (regex #"^/dev/ttys[0-9]+$"))'
    )
    target = directory / "sandbox.sb"
    target.write_text("\n".join(profile))
    return [
        "/usr/bin/sandbox-exec",
        "-f",
        str(target),
        *worker,
    ]


class PackageSession:
    def __init__(
        self,
        runtime: PackageAgentRuntime,
        spec: AgentSpec,
        session_id: str,
        directory: Path,
        process: asyncio.subprocess.Process,
    ):
        self.runtime, self.spec, self.session_id = runtime, spec, session_id
        self.directory, self.process = directory, process
        self.turns: list[Any] = []
        self.events: list[Any] = []
        self.run_ids: list[str] = []
        self.ref: TrajectoryRef | None = None
        self.lock = asyncio.Lock()
        self.closed = False
        self.export_id = (
            new_id("trajectory") if (directory / "session-binding.json").exists() else session_id
        )

    async def send(
        self, message: str, *, event_sink: Callable[[dict[str, Any]], Awaitable[None]] | None = None
    ) -> str:
        async with self.lock:
            if self.closed:
                raise RuntimeError("Session 已结束")
            self.turns.append({"role": "user", "content": message})
            try:
                assert self.process.stdin is not None and self.process.stdout is not None
                self.process.stdin.write((json.dumps({"message": message}) + "\n").encode())
                await self.process.stdin.drain()
                async with asyncio.timeout(self.runtime.timeout):
                    while True:
                        line = await self.process.stdout.readline()
                        if not line:
                            raise RuntimeError(
                                f"Runtime worker exited; see {self.directory / 'worker.log'}"
                            )
                        item = json.loads(line)
                        if item["type"] == "event":
                            self.events.append(item["event"])
                            if event_sink is not None:
                                await event_sink(item["event"])
                        elif item["type"] == "run":
                            self.run_ids.append(item["runId"])
                        elif item["type"] == "result":
                            if item.get("error"):
                                raise RuntimeError(item["error"])
                            text = str(item["text"])
                            self.turns.append({"role": "assistant", "content": text})
                            return text
            except BaseException as exc:
                self.turns.append({"error": type(exc).__name__, "message": str(exc)})
                await self._close()
                await self._save()
                raise

    async def _close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.process.returncode is None:
            # SIGTERM lets the worker cancel the active Run and commit its terminal state.
            with suppress(ProcessLookupError):
                if self.runtime.settings.isolation == "linux-bwrap":
                    descendants = _process_descendants(self.process.pid)
                    os.kill(descendants[0] if descendants else self.process.pid, signal.SIGTERM)
                else:
                    self.process.terminate()
            with suppress(TimeoutError):
                await asyncio.wait_for(self.process.wait(), 10)
            # Also reap native grandchildren on failed or timed-out cleanup.
            if self.process.returncode is None:
                with suppress(ProcessLookupError):
                    os.killpg(self.process.pid, signal.SIGKILL)
                await self.process.wait()
        self.runtime.sessions.discard(self)

    async def _save(self) -> TrajectoryRef:
        if self.ref is None:
            # Export committed facts, including
            # cancellation events emitted while the parent was stopping the worker.
            from areal_rsi.modules.runtime_storage import read_journal

            bindings = self.directory / "run-bindings.json"
            if bindings.exists():
                self.run_ids = json.loads(bindings.read_text())
            events, _snapshots = read_journal(self.directory / "data/state")
            self.events = [
                event.model_dump(mode="json", by_alias=True)
                for run_id in self.run_ids
                for event in events
                if event.run_id == run_id
            ]
            content = await self.runtime.content.put_json(
                {
                    "runtime": "areal-agent-runtime",
                    "session_id": self.events[0]["sessionId"] if self.events else None,
                    "rsi_session_id": self.session_id,
                    "harness": self.runtime.selection["kind"],
                    "agent_id": self.spec.agent_id,
                    "turns": self.turns,
                    "events": self.events,
                    "run_ids": list(self.run_ids),
                    "runtime_data_root": str(self.directory / "data"),
                    "projection": "RSI trajectory export; Runtime StateStore is authoritative",
                }
            )
            usage: dict[str, Any] = {}
            for event in self.events:
                if event.get("type") == "usage.updated":
                    for key, value in event.get("data", {}).get("usage", {}).items():
                        if isinstance(value, (int, float)):
                            usage[key] = usage.get(key, 0) + value
            self.ref = await self.runtime.trajectories.save(
                Trajectory(self.export_id, self.spec.agent_id, content, usage, now())
            )
        return self.ref

    async def finish(self) -> TrajectoryRef:
        async with self.lock:
            await self._close()
            return await self._save()


class PackageAgentRuntime:
    def __init__(
        self,
        root: Path,
        content: FileContentStore,
        trajectories: FileTrajectoryStore,
        *,
        settings: RuntimeSettings,
        role: str,
        base_url: str,
        api_key: str,
        timeout: float = 1200,
        max_tokens: int = 4096,
        dsh_root: Path | None = None,
        credential_env_names: Any = (),
        agent_directories: bool = False,
        **_: Any,
    ):
        self.root, self.content, self.trajectories = root.resolve(), content, trajectories
        self.settings, self.role = settings, role
        self.selection = settings.select(role)
        self.base_url, self.api_key, self.timeout = base_url, api_key, timeout
        self.max_tokens, self.dsh_root = max_tokens, dsh_root
        self.credential_env_names = credential_env_names
        self.agent_directories = agent_directories
        self.mcp_registry: dict[str, dict[str, Any]] = {}
        self.sessions: set[PackageSession] = set()

    async def open(
        self,
        agent: AgentSpec,
        *,
        workspace_root: str | None = None,
        session_id: str | None = None,
        research_tools: dict[str, Any] | None = None,
        codex_config: dict[str, Any] | None = None,
        context_window_tokens: int | None = None,
        workspace_readonly_mounts: tuple[tuple[str, str], ...] = (),
    ) -> PackageSession:
        if agent.runtime not in {"areal-agent-runtime@v1", "deepseek-harness@v1"}:
            raise ValueError(f"Unknown RSI Runtime binding: {agent.runtime}")
        unknown = set(agent.tools) - {"bash@v1", "editor@v1"}
        if unknown:
            raise ValueError(f"Unmapped RSI tools: {sorted(unknown)}")
        session_id = safe_name(session_id) if session_id else new_id("trajectory")
        parent = (
            self.root / safe_name(agent.agent_id) / "sessions"
            if self.agent_directories
            else self.root
        )
        directory = parent / session_id
        directory.mkdir(parents=True, exist_ok=True)
        modules = directory / "worker-code/areal_rsi/modules"
        modules.mkdir(parents=True, exist_ok=True)
        (modules.parent / "__init__.py").write_text("")
        (modules / "__init__.py").write_text("")
        for name in (
            "runtime_worker.py",
            "runtime_mcp.py",
            "runtime_mcp.schema.json",
            "runtime_storage.py",
            "runtime_storage.schema.json",
            "runtime_tools.py",
            "runtime_tools.schema.json",
            "runtime_workspace.py",
            "runtime_workspace.schema.json",
        ):
            shutil.copyfile(Path(__file__).parent / name, modules / name)
        workspace = Path(workspace_root).resolve() if workspace_root else directory / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        readonly_mounts: list[tuple[Path, Path]] = []
        for source_value, target_value in workspace_readonly_mounts:
            source = Path(source_value).expanduser().resolve()
            target_relative = Path(target_value)
            if not source.exists():
                raise FileNotFoundError(f"Read-only workspace mount does not exist: {source}")
            if target_relative.is_absolute() or ".." in target_relative.parts:
                raise ValueError("Read-only workspace mount target must stay inside the workspace")
            target = (workspace / target_relative).resolve()
            if not target.is_relative_to(workspace):
                raise ValueError("Read-only workspace mount target escapes the workspace")
            if not target.exists():
                raise FileNotFoundError(
                    f"Read-only workspace mount target placeholder does not exist: {target}"
                )
            readonly_mounts.append((source, target))
        selection = copy.deepcopy(self.selection)
        readable = list(self.settings.readable_paths)
        if selection["kind"] == "codex-cli" and not selection.get("config", {}).get("codexBin"):
            from codex_cli_bin import bundled_codex_path  # type: ignore[import-untyped]

            selection.setdefault("config", {})["codexBin"] = str(bundled_codex_path())
        if selection["kind"] == "dsh" and not selection.get("config", {}).get("dshBin"):
            from areal_rsi.modules.dsh import ensure_cli

            # Experiment directories link to shared installations. sandbox-exec checks
            # the executable path too, so use the same canonical path as the read grant.
            cli = (await asyncio.to_thread(ensure_cli, self.dsh_root or self.root.parent)).resolve()
            selection.setdefault("config", {}).setdefault("dshBin", str(cli))
            readable.append(cli.absolute().parent.parent.parent.resolve())
        if selection["kind"] == "dsh":
            from areal_rsi.modules.runtime_dsh import ensure_profile

            home = (
                await asyncio.to_thread(ensure_profile, self.dsh_root or self.root.parent)
            ).resolve()
            readable.append(home)
            profile = directory / "dsh-home/profiles/agent-runtime"
            profile.mkdir(parents=True, exist_ok=True)
            shared = home / "profiles/agent-runtime"
            for name in ("package.json", "cordis.yml", "cordis.patch.yml"):
                shutil.copyfile(shared / name, profile / name)
            if not (profile / "node_modules").is_symlink():
                (profile / "node_modules").symlink_to(shared / "node_modules")
            selection.setdefault("config", {}).update(
                dshHome=str(directory / "dsh-home"), profile="agent-runtime"
            )
        missing_mcp = [name for name in agent.mcp_servers if name not in self.mcp_registry]
        if missing_mcp:
            raise ValueError(f"Unknown MCP servers: {missing_mcp}")
        config = {
            "runtime": selection,
            "isolation": self.settings.isolation,
            "directory": str(directory),
            "workspace": str(workspace),
            "agent_id": agent.agent_id,
            "model": agent.model.name,
            "model_version_id": agent.model.version_id,
            "prompts": [await self.content.read_text(ref) for ref in agent.prompt_templates],
            "skills": [await self.content.read_text(ref) for ref in agent.skills],
            "tools": list(agent.tools),
            "mcp": [self.mcp_registry[name] for name in agent.mcp_servers],
            "base_url": self.base_url,
            "max_tokens": self.max_tokens,
        }
        if research_tools is not None:
            config["research_tools"] = research_tools
            config["allow_empty_final"] = True
            bin_dir = directory / "bin"
            bin_dir.mkdir(exist_ok=True)
            helper = bin_dir / "rsi-tools"
            module = str(modules / "runtime_tools.py")
            manifest = str(directory / "research-tools.json")
            helper.write_text(
                f"#!{sys.executable} -I\n"
                "import runpy, sys\n"
                f"sys.argv = [{module!r}, {manifest!r}, *sys.argv[1:]]\n"
                f"runpy.run_path({module!r}, run_name='__main__')\n"
            )
            helper.chmod(0o700)
            tool_manifest = directory / "research-tools.json"
            tool_manifest.write_text(json.dumps(research_tools))
            tool_manifest.chmod(0o600)
        if codex_config is not None:
            config["codex_config"] = codex_config
        if context_window_tokens is not None:
            config["context_window_tokens"] = context_window_tokens
        config_path = directory / "request.json"
        config_path.write_text(json.dumps(config))
        config_path.chmod(0o600)
        # A child must not inherit the desktop agent's session, tools or credentials.
        env = {
            key: value
            for key, value in os.environ.items()
            if key
            in {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR"}
        }
        if self.settings.isolation in {"linux-bwrap", "linux-unrestricted"}:
            for name in (
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "NO_PROXY",
                "http_proxy",
                "https_proxy",
                "all_proxy",
                "no_proxy",
            ):
                if name in os.environ:
                    env[name] = os.environ[name]
            # Node's native fetch needs an explicit opt-in to use these proxies.
            env["NODE_USE_ENV_PROXY"] = "1"
            env["SHELL"] = "/bin/bash"
        auth_name = selection.get("config", {}).get("authTokenEnv")
        if auth_name:
            if auth_name not in os.environ:
                raise ValueError(f"Missing configured remote credential variable: {auth_name}")
            env[auth_name] = os.environ[auth_name]
        for name in ("tmp", "home", "cache"):
            (directory / name).mkdir(exist_ok=True)
        env["PATH"] = (
            str(directory / "bin")
            + os.pathsep
            + str(Path(sys.executable).parent)
            + os.pathsep
            + (
                str(Path(node_binary).resolve().parent) + os.pathsep
                if (node_binary := shutil.which("node"))
                else ""
            )
            + env.get("PATH", "/usr/bin:/bin")
        )
        env.update(
            HOME=str(directory / "home"),
            TMPDIR=str(directory / "tmp"),
            # zsh uses TMPPREFIX, not TMPDIR, for heredoc temporary files.
            TMPPREFIX=str(directory / "tmp/zsh"),
            XDG_CACHE_HOME=str(directory / "cache"),
            RSI_RUNTIME_KEY=self.api_key,
            DSH_TELEMETRY_DISABLED="1",
        )
        interpreter = selection.get("config", {}).get("python")
        command = sandbox_command(
            directory,
            workspace,
            tuple(readable),
            (Path(interpreter),) if interpreter else (),
            tuple(readonly_mounts),
            isolation=self.settings.isolation,
        )
        if research_tools is not None:
            # Fail before spending model calls if the agent's actual shell cannot
            # write heredocs/tempfiles or resolve its scoped task-tool command.
            if self.settings.isolation == "linux-unrestricted":
                shell_command = ["/bin/bash", "-ec"]
            elif self.settings.isolation == "linux-bwrap":
                separator = len(command) - 1 - command[::-1].index("--")
                shell_command = [*command[: separator + 1], "/bin/bash", "-ec"]
            else:
                shell_command = [*command[:3], "/bin/zsh", "-lec"]
            probe = await asyncio.to_thread(
                subprocess.run,
                [
                    *shell_command,
                    "python - <<'PY'\nimport tempfile\n"
                    "with tempfile.TemporaryFile() as f:\n    f.write(b'ok')\nPY\n"
                    "rsi-tools list >/dev/null",
                ],
                cwd=workspace,
                env=env,
                capture_output=True,
                timeout=10,
            )
            if probe.returncode:
                raise RuntimeError(
                    "Agent preflight failed: shell, temporary directory or rsi-tools"
                )
        with (directory / "worker.log").open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                *command,
                str(config_path),
                cwd=workspace,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=log,
                start_new_session=True,
                limit=16 * 1024 * 1024,
            )
        session = PackageSession(self, agent, session_id, directory, process)
        self.sessions.add(session)
        return session

    async def close(self) -> None:
        await asyncio.gather(*(session.finish() for session in tuple(self.sessions)))
