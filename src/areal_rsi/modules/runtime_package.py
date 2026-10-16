"""RSI sessions backed by the public ARealAgentRuntime Python package.

Each conversation owns an embedded composition in a sandboxed Python process.
The process boundary protects RSI's hidden evaluation data from native tools.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import shutil
import signal
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

from areal_rsi.api.base import AgentSpec, Trajectory, TrajectoryRef
from areal_rsi.base.storage import FileContentStore, FileTrajectoryStore, new_id, now


def skill_resource_events(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Separate resource reads/commands from discovery, entrypoints and failed calls."""
    requested = {
        event.get("data", {}).get("callId"): event
        for event in events
        if event.get("type") == "tool.requested"
    }
    result: dict[str, list[dict[str, Any]]] = {
        "reads": [],
        "commands": [],
        "discovery": [],
        "entrypoints": [],
        "failed": [],
        "unverified": [],
    }
    for event in events:
        if event.get("type") not in {"tool.completed", "tool.failed"}:
            continue
        data = event.get("data", {})
        request = requested.get(data.get("callId"))
        arguments = (request or {}).get("data", {}).get("arguments", {})
        if "research-skills/" not in json.dumps(arguments):
            continue
        pair = {"request": request, "completion": event}
        if event.get("type") == "tool.failed" or data.get("error"):
            result["failed"].append(pair)
            continue
        name = data.get("name")
        if name == "workspace.list":
            result["discovery"].append(pair)
        elif name == "workspace.read":
            kind = (
                "entrypoints" if str(arguments.get("path", "")).endswith("/SKILL.md") else "reads"
            )
            result[kind].append(pair)
        elif name == "workspace.run":
            value = data.get("value")
            exit_code = (
                value.get("exitCode", value.get("exit_code")) if isinstance(value, dict) else None
            )
            if type(exit_code) is int and exit_code == 0:
                result["commands"].append(pair)
            elif type(exit_code) is int:
                result["failed"].append(pair)
            else:
                result["unverified"].append(pair)
    return result


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
        self.default = copy.deepcopy(value.get("default", {"kind": "dsh", "config": {}}))
        self.roles = copy.deepcopy(value.get("roles", {}))
        if not isinstance(self.default, dict) or not isinstance(self.roles, dict):
            raise ValueError("Runtime default and roles must be objects")
        if set(self.roles) - {"rsi-agent", "research-agent", "task-agent"}:
            raise ValueError("Unknown Runtime role")
        self.readable_paths = tuple(
            Path(p).expanduser().resolve() for p in value.get("readable_paths", [])
        )
        self.executables: dict[Path, tuple[tuple[int, ...] | None, str | None]] = {}
        for selection in (self.default, *self.roles.values()):
            if not isinstance(selection, dict) or selection.get("kind") not in {
                "dsh",
                "codex-cli",
                "claude-code",
                "claude-agent-sdk",
                "hermes",
            }:
                continue
            config = selection.get("config", {})
            if not isinstance(config, dict):
                raise ValueError("Runtime adapter config must be an object")
            for name in ("dshBin", "codexBin", "cliPath", "python"):
                configured = config.get(name)
                if isinstance(configured, str) and configured:
                    path = Path(shutil.which(configured) or configured).expanduser().absolute()
                    self.executables[path] = self.fingerprint(path)
        provenance = Path(__file__).parents[1] / "resources/runtime/provenance.json"
        identity_config = copy.deepcopy(value)
        if self.executables:
            identity_config["local_executable_digests"] = {
                str(path): fingerprint[1] for path, fingerprint in self.executables.items()
            }
        self.identity = hashlib.sha256(
            json.dumps(identity_config, sort_keys=True).encode() + provenance.read_bytes()
        ).hexdigest()

    @staticmethod
    def signature(path: Path) -> tuple[int, ...]:
        info = path.stat()
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

    @classmethod
    def fingerprint(cls, path: Path) -> tuple[tuple[int, ...] | None, str | None]:
        if not path.is_file():
            return None, None
        before = cls.signature(path)
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if cls.signature(path) != before:
            raise ValueError("Runtime executable changed while its identity was being frozen")
        return before, digest

    def validate_artifacts(self) -> None:
        for path, (signature, digest) in self.executables.items():
            if not path.is_file():
                raise FileNotFoundError(f"Configured Runtime executable is unavailable: {path}")
            if self.signature(path) != signature:
                current = self.fingerprint(path)
                if current[1] != digest:
                    raise ValueError("Runtime executable changed; a new Goal revision is required")
                self.executables[path] = current

    @classmethod
    def from_environment(cls) -> RuntimeSettings:
        path = os.environ.get("RSI_RUNTIME_CONFIG")
        return cls(json.loads(Path(path).expanduser().read_text()) if path else {})

    def select(self, role: str) -> dict[str, Any]:
        role = "research-agent" if role.startswith("research-agent-") else role
        result: dict[str, Any] = copy.deepcopy(self.roles.get(role, self.default))
        from areal_agent_runtime import RuntimeBootstrapConfig

        result = RuntimeBootstrapConfig.model_validate(result).model_dump(
            mode="json", by_alias=True, exclude_unset=True
        )
        for name in (
            "dshBin",
            "codexBin",
            "cliPath",
            "python",
            "hermesRoot",
            "caFile",
            "clientCertFile",
            "clientKeyFile",
        ):
            path = result.get("config", {}).get(name)
            if isinstance(path, str):
                if name in {"dshBin", "codexBin", "cliPath", "python"}:
                    result["config"][name] = str(
                        Path(shutil.which(path) or path).expanduser().absolute()
                    )
                elif path.startswith("~"):
                    result["config"][name] = str(Path(path).expanduser())
        scanner = result.get("config", {}).get("env", {}).get("TIRITH_BIN")
        if isinstance(scanner, str) and scanner.startswith("~"):
            result["config"]["env"]["TIRITH_BIN"] = str(Path(scanner).expanduser())
        return result


def sandbox_command(
    directory: Path,
    workspace: Path,
    readable: tuple[Path, ...],
    executable_aliases: tuple[Path, ...] = (),
    *,
    payload: list[str] | None = None,
) -> list[str]:
    if sys.platform.startswith("linux"):
        roots = {
            str(Path(sys.prefix).resolve()),
            str(Path(sys.base_prefix).resolve()),
            str(Path(sys.executable).resolve().parent.parent),
            "/usr",
            "/bin",
            "/lib",
            "/lib64",
            "/etc",
            "/dev",
            *(str(p.resolve()) for p in readable),
        }
        for executable in (Path(sys.executable), *executable_aliases):
            if executable.is_symlink():
                alias = Path(os.readlink(executable))
                if not alias.is_absolute():
                    alias = executable.parent / alias
                roots.add(str(alias.parent.parent))
        node = shutil.which("node")
        if node:
            roots.add(str(Path(node).resolve().parent.parent))
        shutil.copyfile(
            Path(__file__).with_name("linux_sandbox.py"), directory / "linux-sandbox.py"
        )
        policy = directory / "namespace.json"
        policy.write_text(
            json.dumps(
                {
                    "readable": sorted(roots),
                    "writable": [str(directory), str(workspace)],
                    "worker_code": str(directory / "worker-code"),
                    "rootfs": str(directory / "rootfs"),
                    "workspace": str(workspace),
                    "payload": payload,
                    "readonly_resources": [str(workspace / "research-skills")]
                    if (workspace / "research-skills").exists()
                    else [],
                }
            )
        )
        return [
            "unshare",
            "--user",
            "--map-root-user",
            "--mount",
            "--pid",
            "--fork",
            sys.executable,
            "-I",
            str(directory / "linux-sandbox.py"),
            str(policy),
        ]
    if sys.platform != "darwin":
        raise RuntimeError("RSI Runtime requires macOS sandbox-exec or Linux namespaces")
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
        if executable.is_symlink():
            alias = Path(os.readlink(executable))
            if not alias.is_absolute():
                alias = executable.parent / alias
            profile.append(
                f"(allow file-read-metadata (subpath {json.dumps(str(alias.parent.parent))}))"
            )
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
    if payload is not None:
        return ["/usr/bin/sandbox-exec", "-f", str(target), *payload]
    return [
        "/usr/bin/sandbox-exec",
        "-f",
        str(target),
        sys.executable,
        "-I",
        "-c",
        "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));"
        "runpy.run_module('areal_rsi.modules.runtime_worker',run_name='__main__')",
        str(directory / "worker-code"),
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

    async def send(self, message: str) -> str:
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
                os.killpg(self.process.pid, signal.SIGTERM)
            with suppress(TimeoutError):
                await asyncio.wait_for(self.process.wait(), 10)
            # Also reap native grandchildren on failed or timed-out cleanup.
            if self.process.returncode is None:
                with suppress(ProcessLookupError):
                    os.killpg(self.process.pid, signal.SIGKILL)
                await self.process.wait()
        if self.process.stdin is not None:
            self.process.stdin.close()
            with suppress(BrokenPipeError, ConnectionResetError):
                await self.process.stdin.wait_closed()
        if self.process.stdout is not None:
            with suppress(TimeoutError):
                await asyncio.wait_for(self.process.stdout.read(), 10)
        self.runtime.sessions.discard(self)

    async def _save(self) -> TrajectoryRef:
        if self.ref is None:
            # Rebuild the compatibility export from committed facts, including
            # cancellation events emitted while the parent was stopping the worker.
            from areal_agent_runtime.adapters.storage import FileSystemStateStore

            bindings = self.directory / "run-bindings.json"
            if bindings.exists():
                self.run_ids = json.loads(bindings.read_text())
            state = FileSystemStateStore(self.directory / "data/state")
            self.events = [
                event.model_dump(mode="json", by_alias=True)
                for run_id in self.run_ids
                for event in await state.list_events(run_id)
            ]
            completed_tools = [e for e in self.events if e.get("type") == "tool.completed"]
            loaded = [e for e in completed_tools if e.get("data", {}).get("name") == "skill"]
            resource_calls: Any = skill_resource_events(self.events)
            resources: Any = [*resource_calls["reads"], *resource_calls["commands"]]
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
                    "projection": "RSI compatibility export; Runtime StateStore is authoritative",
                    "research_skills": {
                        "configured": [ref.digest for ref in self.spec.skills],
                        "loaded_events": loaded,
                        "resource_events": resources,
                        "resource_calls": resource_calls,
                        "semantic_effect": "unknown",
                    },
                }
            )
            usage: dict[str, Any] = {}
            observations = []
            for index, event in enumerate(self.events):
                if event.get("type") != "usage.updated":
                    continue
                following = self.events[index + 1] if index + 1 < len(self.events) else {}
                if (
                    self.runtime.selection["kind"] == "dsh"
                    and following.get("data", {}).get("vendorEventType")
                    != "assistant/message.usage"
                ):
                    # DSH also projects the same usage from assistant/message.
                    # Retain both canonical events, aggregate only the dedicated usage notification.
                    continue
                observations.append(event.get("data", {}).get("usage", {}))
            for observation in observations:
                for key, value in observation.items():
                    if type(value) in (int, float):
                        usage[key] = (
                            value if observation.get("cumulative") else usage.get(key, 0) + value
                        )
            usage["observed_calls"] = len(observations)
            usage["aggregation"] = "dedicated_usage_notifications" if observations else "unknown"
            self.ref = await self.runtime.trajectories.save(
                Trajectory(self.session_id, self.spec.agent_id, content, usage, now())
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
        **_: Any,
    ):
        self.root, self.content, self.trajectories = root.resolve(), content, trajectories
        self.settings, self.role = settings, role
        self.selection = settings.select(role)
        self.base_url, self.api_key, self.timeout = base_url, api_key, timeout
        self.max_tokens, self.dsh_root = max_tokens, dsh_root
        self.credential_env_names = credential_env_names
        self.mcp_registry: dict[str, dict[str, Any]] = {}
        self.sessions: set[PackageSession] = set()
        self.closing = False

    async def open(self, agent: AgentSpec, *, workspace_root: str | None = None) -> PackageSession:
        if self.closing:
            raise RuntimeError("Runtime is closing; no new work may be dispatched")
        self.settings.validate_artifacts()
        if agent.runtime not in {"areal-agent-runtime@v1", "deepseek-harness@v1"}:
            raise ValueError(f"Unknown RSI Runtime binding: {agent.runtime}")
        unknown = set(agent.tools) - {"bash@v1", "editor@v1"}
        if unknown:
            raise ValueError(f"Unmapped RSI tools: {sorted(unknown)}")
        if agent.skills and self.selection["kind"] != "dsh":
            raise ValueError("This Runtime adapter has not passed Skill resource capability checks")
        session_id = new_id("trajectory")
        directory = self.root / session_id
        directory.mkdir(parents=True)
        modules = directory / "worker-code/areal_rsi/modules"
        modules.mkdir(parents=True)
        (modules.parent / "__init__.py").write_text("")
        (modules / "__init__.py").write_text("")
        for name in (
            "runtime_worker.py",
            "runtime_mcp.py",
            "runtime_mcp.schema.json",
            "linux_sandbox.py",
        ):
            shutil.copyfile(Path(__file__).parent / name, modules / name)
        workspace = Path(workspace_root).resolve() if workspace_root else directory / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        selection = copy.deepcopy(self.selection)
        readable = list(self.settings.readable_paths)
        if selection["kind"] == "dsh" and not selection.get("config", {}).get("dshBin"):
            from areal_rsi.modules.dsh import ensure_cli

            cli = await asyncio.to_thread(ensure_cli, self.dsh_root or self.root.parent)
            selection.setdefault("config", {}).setdefault("dshBin", str(cli))
            readable.append(cli.absolute().parent.parent.parent.resolve())
        if selection["kind"] == "dsh":
            readable.append(Path(selection["config"]["dshBin"]).resolve().parent)
            from areal_rsi.modules.runtime_dsh import ensure_profile

            home = await asyncio.to_thread(
                ensure_profile,
                self.dsh_root or self.root.parent,
                binary=Path(selection["config"]["dshBin"]),
            )
            readable.append(home)
            profile = directory / "dsh-home/profiles/agent-runtime"
            profile.mkdir(parents=True)
            shared = home / "profiles/agent-runtime"
            for name in ("package.json", "cordis.yml", "cordis.patch.yml"):
                shutil.copyfile(shared / name, profile / name)
            (profile / "node_modules").symlink_to(shared / "node_modules")
            closure = home / "profiles/node_modules"
            if closure.exists():
                (profile.parent / "node_modules").symlink_to(closure)
            selection.setdefault("config", {}).update(
                dshHome=str(directory / "dsh-home"), profile="agent-runtime"
            )
        missing_mcp = [name for name in agent.mcp_servers if name not in self.mcp_registry]
        if missing_mcp:
            raise ValueError(f"Unknown MCP servers: {missing_mcp}")
        config = {
            "runtime": selection,
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
        auth_name = selection.get("config", {}).get("authTokenEnv")
        if auth_name:
            if auth_name not in os.environ:
                raise ValueError(f"Missing configured remote credential variable: {auth_name}")
            env[auth_name] = os.environ[auth_name]
        for name in ("tmp", "home", "cache"):
            (directory / name).mkdir()
        env["PATH"] = (
            str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "/usr/bin:/bin")
        )
        env.update(
            HOME=str(directory / "home"),
            TMPDIR=str(directory / "tmp"),
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
        if self.closing:
            await session.finish()
            raise RuntimeError("Runtime closed while the session was starting")
        return session

    async def close(self) -> None:
        self.closing = True
        await asyncio.gather(*(session.finish() for session in tuple(self.sessions)))
