"""通过官方 Python SDK 驱动 DSH 的完整 Agent loop。"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from deepseek_harness import DeepSeekHarness  # type: ignore[import-untyped]
from deepseek_harness_runtime import bundled_runtime_path  # type: ignore[import-untyped]

from areal_rsi.api.base import AgentSpec, Trajectory, TrajectoryRef
from areal_rsi.base.storage import FileContentStore, FileTrajectoryStore, new_id, now


def sandbox_wrapper(directory: Path, workspace: Path) -> Path:
    """MVP 的 macOS sandbox-exec 继承到所有 Bash/编辑器子进程。"""
    if sys.platform != "darwin":
        raise RuntimeError("当前本地隔离实现需要 macOS sandbox-exec；其他平台需接入隔离实现")
    runtime = bundled_runtime_path().resolve()
    # 禁止读用户目录、临时目录；只暴露本次 session 和 SDK 安装内容。
    roots = {
        str(directory.resolve()),
        str(workspace.resolve()),
        str(Path(sys.prefix).resolve()),
        str(Path(sys.base_prefix).resolve()),
    }
    profile = [
        "(version 1)",
        "(allow default)",
        '(deny file-read* (subpath "/Users") (subpath "/private/tmp") '
        '(subpath "/private/var/folders"))',
        "(deny file-write*)",
    ]
    for root in sorted(roots):
        profile.append(f"(allow file-read* (subpath {json.dumps(root)}))")
    for writable in (directory.resolve(), workspace.resolve()):
        profile.append(f"(allow file-write* (subpath {json.dumps(str(writable))}))")
    profile.append(
        '(allow file-read* file-write* (literal "/dev/null") (literal "/dev/tty") '
        '(literal "/dev/ptmx") '
        '(regex #"^/dev/ttys[0-9]+$"))'
    )
    profile_path = directory / "sandbox.sb"
    profile_path.write_text("\n".join(profile))
    wrapper = directory / "dsh"
    wrapper.write_text(
        "#!/bin/sh\nexec /usr/bin/sandbox-exec -f "
        + shlex.quote(str(profile_path))
        + " "
        + shlex.quote(str(runtime))
        + ' "$@"\n'
    )
    wrapper.chmod(0o700)
    return wrapper


class DshSession:
    def __init__(
        self,
        sdk: DeepSeekHarness,
        spec: AgentSpec,
        session_id: str,
        content: FileContentStore,
        trajectories: FileTrajectoryStore,
    ):
        self.sdk = sdk
        self.spec = spec
        self.session_id = session_id
        self.content = content
        self.trajectories = trajectories
        self.events: list[Any] = []
        self.turns: list[Any] = []
        self.usage: dict[str, Any] = {}
        self.ref: TrajectoryRef | None = None
        self.lock = asyncio.Lock()

    async def send(self, message: str) -> str:
        async with self.lock:
            if self.ref is not None:
                raise RuntimeError("Session 已结束")
            self.turns.append({"role": "user", "content": message})
            try:
                result = await asyncio.to_thread(self.sdk.run, message, session_id=self.session_id)
                self.events.extend(result.events)
                for event in result.events:
                    chunk = event.get("data", {}).get("chunk", {})
                    if chunk.get("type") == "usage":
                        for key, value in chunk.get("usage", {}).items():
                            if isinstance(value, (int, float)):
                                self.usage[key] = self.usage.get(key, 0) + value
                self.turns.append(
                    {
                        "role": "assistant",
                        "content": result.final_response,
                        "finish_reason": result.finish_reason,
                    }
                )
                if result.finish_reason != "completed":
                    raise RuntimeError(f"DSH turn 未完成：{result.finish_reason}")
                return str(result.final_response)
            except BaseException as exc:
                self.turns.append({"error": type(exc).__name__, "message": str(exc)})
                await asyncio.to_thread(self.sdk.close)
                await self._save()
                raise

    async def _save(self) -> TrajectoryRef:
        if self.ref is None:
            content = await self.content.put_json(
                {
                    "runtime": "deepseek-harness-sdk",
                    "session_id": self.session_id,
                    "agent_id": self.spec.agent_id,
                    "turns": self.turns,
                    "events": self.events,
                }
            )
            self.ref = await self.trajectories.save(
                Trajectory(
                    self.session_id,
                    self.spec.agent_id,
                    content,
                    self.usage,
                    now(),
                )
            )
        return self.ref

    async def finish(self) -> TrajectoryRef:
        async with self.lock:
            await asyncio.to_thread(self.sdk.close)
            return await self._save()


class DshAgentRuntime:
    def __init__(
        self,
        root: Path,
        content: FileContentStore,
        trajectories: FileTrajectoryStore,
        *,
        base_url: str,
        api_key: str,
        timeout: float = 240,
        max_tokens: int = 4096,
        mcp_registry: dict[str, dict[str, Any]] | None = None,
        tool_registry: dict[str, list[dict[str, Any]]] | None = None,
    ):
        self.root = root.resolve()
        self.content = content
        self.trajectories = trajectories
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.mcp_registry = mcp_registry or {}
        self.tool_registry = tool_registry or {}
        if any(not plugins for plugins in self.tool_registry.values()):
            raise ValueError("已注册工具必须绑定至少一个实际 DSH 插件")

    async def open(self, agent: AgentSpec, *, workspace_root: str | None = None) -> DshSession:
        if agent.runtime != "deepseek-harness@v1":
            raise ValueError(f"未知 Runtime：{agent.runtime}")
        unknown = set(agent.tools) - {"bash@v1", "editor@v1"} - self.tool_registry.keys()
        if unknown:
            raise ValueError(f"未注册工具：{unknown}")
        session_id = new_id("trajectory")
        directory = self.root / session_id
        directory.mkdir(parents=True)
        workspace = Path(workspace_root).resolve() if workspace_root else directory / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        temporary = directory / "tmp"
        temporary.mkdir()
        prompt_parts = [await self.content.read_text(ref) for ref in agent.prompt_templates]
        for i, ref in enumerate(agent.skills):
            skill = await self.content.read_text(ref)
            path = workspace / "skills" / f"skill_{i}.md"
            path.parent.mkdir(exist_ok=True)
            path.write_text(skill)
            prompt_parts.append(f"Skill {i}:\n{skill}")
        prompt = "\n\n".join(prompt_parts) or "You are a helpful assistant."
        patch: list[dict[str, Any]] = [
            {
                "id": "agent-spine",
                "config": {
                    "persona": prompt,
                    "toolBash": False,
                },
            },
            {"id": "persistent-bash", "disabled": True},
            {"id": "str-replace-editor", "disabled": "editor@v1" not in agent.tools},
        ]
        if "bash@v1" in agent.tools:
            # macOS 拒绝在沙箱中执行 setuid /bin/ps，持久终端依赖它。
            # 使用 DSH 官方的非持久 Bash 插件，保持同一进程级目录隔离。
            patch.append(
                {
                    "insert": [
                        {
                            "id": "rsi-bash",
                            "name": "@deepseek-ai/dsh-bash-local",
                            "config": {
                                "cwd": str(workspace),
                                "timeoutMs": 30000,
                                "maxTimeoutMs": 60000,
                                "maxOutputBytes": 8000,
                                "maxSpillBytes": 1000000,
                            },
                        },
                        {"id": "rsi-shell-env", "name": "@deepseek-ai/dsh-shell-env"},
                        {
                            "id": "rsi-tool-bash",
                            "name": "@deepseek-ai/dsh-tool-bash",
                            "config": {"enableRunInBackground": False},
                        },
                    ]
                }
            )
        for name in agent.tools:
            if name in self.tool_registry:
                patch.append({"insert": json.loads(json.dumps(self.tool_registry[name]))})
        for i, name in enumerate(agent.mcp_servers):
            if name not in self.mcp_registry:
                raise ValueError(f"未注册 MCP：{name}")
            patch.append(
                {
                    "insert": [
                        {
                            "id": f"rsi-mcp-{i}",
                            "name": "@deepseek-ai/dsh-mcp-client",
                            "config": self.mcp_registry[name],
                        }
                    ]
                }
            )
        patch_file = directory / "agent.patch.yml"
        patch_file.write_text(yaml.safe_dump(patch, allow_unicode=True))
        wrapper = sandbox_wrapper(directory, workspace)
        sdk = DeepSeekHarness(
            provider="deepseek-official",
            model=agent.model.name,
            profile="sdk-minimal",
            max_tokens=self.max_tokens,
            cwd=str(workspace),
            runtime_cwd=str(workspace),
            dsh_home=str(directory / "dsh-home"),
            dsh_bin=str(wrapper),
            patches=(str(patch_file),),
            base_url=self.base_url,
            api_key=self.api_key,
            request_timeout_seconds=self.timeout,
            initialize_timeout_seconds=45,
            env={
                # SDK 默认继承宿主环境；研究工具不应拿到宿主凭证。
                # 显式 api_key 在 SDK 内最后注入，仍可访问本次专用推理代理。
                **{
                    key: ""
                    for key in os.environ
                    if any(
                        part in key.upper()
                        for part in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "AUTH", "COOKIE")
                    )
                },
                "DSH_TELEMETRY_DISABLED": "1",
                "DSH_SYSTEM_PROMPT": prompt,
                "TMPDIR": str(temporary),
                "PKG_NATIVE_CACHE_PATH": str(directory / "native-cache"),
                "XDG_CACHE_HOME": str(directory / "cache"),
                "DSH_CONTEXT_WINDOW": "32768",
            },
        )
        return DshSession(sdk, agent, session_id, self.content, self.trajectories)
