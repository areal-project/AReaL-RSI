"""Private RSI IPC host embedding only public Runtime APIs; no HTTP server."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import shlex
import signal
import sys
from pathlib import Path
from typing import Any

from areal_agent_runtime import BootstrapConfig, build_composition
from areal_agent_runtime.contracts import CancelRunCommand, StartRunCommand


def compile_config(request: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    directory, workspace = Path(request["directory"]), Path(request["workspace"])
    runtime = copy.deepcopy(request["runtime"])
    kind = runtime["kind"]
    native = kind in {"codex-cli", "hermes"} or runtime.get("profileBinding") == "runtime-owned"
    if native and request["mcp"] and "bash@v1" not in request["tools"]:
        raise ValueError(
            "Native MCP access requires explicit bash@v1 authorization; "
            "use a portable Runtime for MCP-only or read-only sessions"
        )
    runtime.setdefault("selector", "primary")
    runtime.setdefault("profileBinding", "runtime-owned" if native else "portable")
    vendor = runtime.setdefault("config", {})
    key = os.environ.get("RSI_RUNTIME_KEY", "local-proxy")
    base, model = request["base_url"], request["model"]
    if kind == "dsh":
        vendor.update(
            model=model,
            baseUrl=base,
            apiKey=key,
            maxTokens=request["max_tokens"],
            applicationToolBridge=True,
        )
        vendor.setdefault("profile", "sdk-minimal")
        vendor.setdefault("dshHome", str(directory / "dsh-home"))
        vendor.setdefault("runtimeCwd", str(workspace))
        if request.get("research_tools") is not None:
            # Configure the installed DSH provider, rather than implementing a
            # separate Responses loop. Hand-declared model names need explicit
            # reasoning capabilities (e.g. Astra/Luna on the existing gateway).
            requested = vendor.get("reasoningEffort", "high")
            effort = {"none": "off", "max": "xhigh"}.get(requested, requested)
            route = "rsi-responses"
            patch = directory / "research-model.patch.json"
            patch.write_text(
                json.dumps(
                    [
                        {
                            "id": "llm-pi-ai",
                            "config": {
                                "providers": {
                                    route: {
                                        "api": "openai-responses",
                                        "apiKeyEnv": "RSI_RUNTIME_KEY",
                                        "baseURL": base,
                                        "models": [
                                            {
                                                "id": model,
                                                "contextWindow": request.get(
                                                    "context_window_tokens", 256000
                                                ),
                                                "maxTokens": request["max_tokens"],
                                                "reasoningEfforts": {
                                                    effort: None if effort == "off" else requested
                                                },
                                            }
                                        ],
                                    }
                                }
                            },
                        }
                    ]
                )
            )
            vendor.update(provider=route, reasoningEffort=effort)
            vendor["patches"] = [*vendor.get("patches", []), str(patch)]
    elif kind == "anthropic":
        vendor.update(model=model, baseUrl=base, apiKey=key, maxTokens=request["max_tokens"])
    elif kind in {"claude-code", "claude-agent-sdk"}:
        vendor.update(model=model, applicationToolBridge=not native)
        vendor.setdefault("settingSources", [])
        vendor.setdefault("permissionMode", "dontAsk")
        vendor.setdefault("env", {}).update(ANTHROPIC_BASE_URL=base, ANTHROPIC_API_KEY=key)
    elif kind == "codex-cli":
        vendor.update(model=model, baseUrl=base, apiKeyEnv="RSI_RUNTIME_KEY")
        vendor.setdefault("provider", "rsi")
        (directory / "codex-home").mkdir(exist_ok=True)
        vendor.setdefault("codexHome", str(directory / "codex-home"))
        vendor.setdefault("stateDirectory", str(directory / "codex-state"))
        vendor.setdefault("approvalPolicy", "never")
        vendor.setdefault("sandbox", "danger-full-access")
        if request.get("codex_config") is not None:
            # Native configuration belongs to the isolated Codex home. The
            # package owns App Server, provider wiring, threads and compaction.
            home = Path(vendor["codexHome"])
            home.mkdir(parents=True, exist_ok=True)
            (home / "config.toml").write_text(
                "\n".join(
                    f"{name} = {json.dumps(value)}"
                    for name, value in request["codex_config"].items()
                )
                + "\n"
            )
    elif kind == "hermes":
        home = directory / "hermes-home"
        home.mkdir(exist_ok=True)
        (home / "config.yaml").write_text(
            json.dumps(
                {
                    "model": {"default": model, "provider": "custom:rsi"},
                    "providers": {
                        "rsi": {"base_url": base, "api_key": key, "api_mode": "chat_completions"}
                    },
                    "terminal": {"backend": "local"},
                    "memory": {"memory_enabled": False, "user_profile_enabled": False},
                }
            )
        )
        vendor.setdefault("env", {}).update(HERMES_HOME=str(home), HERMES_IGNORE_RULES="1")
        vendor.setdefault("cwd", str(workspace))
    tools = []
    if not native:
        if request["tools"]:
            tools = ["workspace.list", "workspace.read", "workspace.search"]
        if "bash@v1" in request["tools"]:
            tools.append("workspace.run")
        if "editor@v1" in request["tools"]:
            tools.extend(["workspace.write", "workspace.edit"])
    plugins = []
    if kind == "dsh" and request["skills"]:
        plugins.append(
            {"entrypoint": "areal_agent_runtime.adapters.dsh.skill_plugin:create_plugin"}
        )
        tools.append("skill")
    prompts = list(request["prompts"])
    if bridge := request.get("research_tools"):
        if native:
            manifest = directory / "research-tools.json"
            manifest.write_text(json.dumps(bridge))
            manifest.chmod(0o600)
            tool_command = shlex.join(
                [
                    sys.executable,
                    "-I",
                    str(Path(__file__).with_name("runtime_tools.py")),
                    str(manifest),
                ]
            )
            prompts.append(
                "RSI 任务工具通过以下命令调用，使用你的原生命令工具执行。\n"
                f"查看工具及参数：{tool_command} list\n"
                f"调用：{tool_command} call TOOL 'JSON_ARGUMENTS' CALL_ID\n"
                "每次新操作使用新的 CALL_ID；重试同一操作时沿用原 ID。\n"
                "常用工具：" + ", ".join(tool["name"] for tool in bridge["tools"])
            )
        else:
            plugins.append(
                {
                    "entrypoint": "areal_rsi.modules.runtime_tools:create_plugin",
                    "config": bridge,
                }
            )
            tools.extend(tool["name"] for tool in bridge["tools"])
    if request["mcp"] and native:
        # Native-only adapters cannot accept Application ToolSpecs. The installed
        # RSI CLI gives their shell tools access to the same bounded MCP servers.
        manifest = directory / "mcp.json"
        manifest.write_text(json.dumps(request["mcp"]))
        prompts.append(
            "MCP tools are available through the installed RSI MCP client. "
            f"Run {sys.executable} -I {Path(__file__).with_name('runtime_mcp.py')} {manifest} list "
            "to inspect tool schemas; replace list with call SERVER TOOL JSON_ARGUMENTS to invoke. "
            "Use your shell tool (or workspace.run with argv)."
        )
    configuration = {
        "dataRoot": str(directory / "data"),
        "storage": {
            "entrypoint": "areal_rsi.modules.runtime_storage:create_plugin",
        },
        # RSI exports StateStore events directly. The default trajectory sink
        # would add a second projection with its own SQLite index.
        "eventSinks": [],
        "runtime": runtime,
        "host": {"kind": "native", "defaultWorkspaceMount": str(workspace)},
        "environment": {
            "entrypoint": "areal_agent_runtime.adapters.environment.plugin:create_plugin",
            "config": {"allowExternalMounts": True, "allowedExternalMountRoots": [str(workspace)]},
        },
        "workspaceTools": {
            "entrypoint": "areal_agent_runtime.adapters.workspace.plugin:create_plugin",
            "enabled": bool(request["tools"]),
            "config": {
                "allowedCommands": ["sh", "python", "python3", "git"],
                "commandTimeoutSeconds": 60,
                "maxOutputChars": 32000,
            },
        },
        "plugins": plugins,
        "toolBindings": [{"profileId": "general.default", "toolNames": tools}] if tools else [],
    }
    invocation = {
        "systemPrompts": [
            {"kind": "inline", "resourceId": f"rsi-prompt-{i}", "content": text}
            for i, text in enumerate(prompts)
        ],
        "skills": [
            {
                "kind": "inline",
                "resourceId": f"rsi-skill-{i}",
                "mediaType": "text/markdown",
                "content": text,
            }
            for i, text in enumerate(request["skills"])
        ],
    }
    command = {
        "profile": "general.default",
        "runtime": {"selector": runtime["selector"], "model": model},
        "workspace": {"workspaceId": directory.name, "mountHint": str(workspace)},
        "agentEnvironment": {"invocation": invocation},
        "metadata": {
            "rsi": {
                "agentId": request["agent_id"],
                "modelVersionId": request["model_version_id"],
                "trajectoryId": directory.name,
            }
        },
    }
    return configuration, command


async def serve(request: dict[str, Any], output: Any) -> None:
    def emit(item: dict[str, Any]) -> None:
        output.write(json.dumps(item, ensure_ascii=False) + "\n")
        output.flush()

    config, template = compile_config(request)
    if request["mcp"] and config["runtime"]["profileBinding"] != "runtime-owned":
        from areal_rsi.modules.runtime_mcp import discover

        catalog = await discover(request["mcp"])
        config["plugins"].append(
            {
                "entrypoint": "areal_rsi.modules.runtime_mcp:create_plugin",
                "config": {"servers": request["mcp"], "catalog": catalog},
            }
        )
        if catalog:
            names = [entry["spec"]["name"] for entry in catalog]
            if config["toolBindings"]:
                config["toolBindings"][0]["toolNames"].extend(names)
            else:
                config["toolBindings"] = [{"profileId": "general.default", "toolNames": names}]
    composition = await build_composition(BootstrapConfig.model_validate(config))
    app = composition.application
    active: str | None = None
    binding = Path(request["directory"]) / "session-binding.json"
    saved = json.loads(binding.read_text()) if binding.exists() else {}
    session_id: str | None = saved.get("session_id")
    bindings = Path(request["directory"]) / "run-bindings.json"
    run_ids: list[str] = json.loads(bindings.read_text()) if bindings.exists() else []
    try:
        reader = asyncio.StreamReader(limit=16 * 1024 * 1024)
        owner = asyncio.current_task()

        class ParentPipeProtocol(asyncio.StreamReaderProtocol):
            def connection_lost(self, exc: Exception | None) -> None:
                super().connection_lost(exc)
                # Losing the coordinator must cancel native execution too.
                if active is not None and owner is not None:
                    owner.cancel()

        protocol = ParentPipeProtocol(reader)
        await asyncio.get_running_loop().connect_read_pipe(lambda: protocol, sys.stdin)
        while line := await reader.readline():
            message = json.loads(line)["message"]
            command = copy.deepcopy(template)
            command["input"] = [{"type": "text", "text": message}]
            if session_id:
                command.update(sessionId=session_id, resumeSession=True)
            ref = await app.start_run(StartRunCommand.model_validate(command))
            active, session_id = ref.run_id, ref.session_id
            temporary_binding = binding.with_suffix(".tmp")
            temporary_binding.write_text(json.dumps({"session_id": session_id}))
            temporary_binding.replace(binding)
            run_ids.append(active)
            bindings = Path(request["directory"]) / "run-bindings.json"
            temporary = bindings.with_suffix(".tmp")
            temporary.write_text(json.dumps(run_ids))
            temporary.replace(bindings)
            emit({"type": "run", "runId": active, "sessionId": session_id})
            text: list[str] = []
            async for event in app.stream_events(active):
                data = event.model_dump(mode="json", by_alias=True)
                emit({"type": "event", "event": data})
                if data["type"] == "message.completed" and not data.get("parentAgentId"):
                    payload = data["data"]
                    if payload.get("role", "assistant") == "assistant":
                        text = list(
                            b["text"] for b in payload.get("content", []) if b.get("type") == "text"
                        )
                if data["type"] in {"run.completed", "run.failed", "run.cancelled"}:
                    break
                if data["type"] == "interaction.required":
                    await app.cancel_run(
                        CancelRunCommand(run_id=active, reason="Unattended RSI worker")
                    )
            snapshot = await app.wait(active)
            active = None
            error = (
                None
                if snapshot.status == "completed" and (text or request.get("allow_empty_final"))
                else str(snapshot.error or f"Run {snapshot.status}: no final assistant text")
            )
            emit({"type": "result", "text": "\n".join(text), "error": error})
    finally:
        if active:
            await app.cancel_run(CancelRunCommand(run_id=active, reason="RSI worker stopped"))
        await composition.close()


def main() -> None:
    request = json.loads(Path(sys.argv[1]).read_text())
    output = sys.stdout
    # Vendor libraries may print diagnostics; the private IPC stream stays JSON-only.
    sys.stdout = sys.stderr

    async def run() -> None:
        task = asyncio.current_task()
        assert task is not None
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
        try:
            await serve(request, output)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            output.write(json.dumps({"type": "result", "text": "", "error": str(exc)}) + "\n")
            output.flush()
            raise

    asyncio.run(run())


if __name__ == "__main__":
    main()
