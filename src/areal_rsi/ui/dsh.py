"""Native DSH conversation frontend; research execution stays in the workbench."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import signal
import subprocess
from pathlib import Path

from areal_rsi.base.storage import new_id
from areal_rsi.config import AgentModelSettings
from areal_rsi.coordination.config import load_config
from areal_rsi.modules.dsh_config import write_profile_patch
from areal_rsi.modules.dsh_profile import PROFILE, ensure_profile
from areal_rsi.modules.gateway import GatewayProxy
from areal_rsi.ui.launch import ensure_workbench
from areal_rsi.ui.mcp import ResearchBridge


def launch_tui(
    root: Path,
    *,
    port: int = 8197,
    config: Path | None = None,
    resume: str | None = None,
    session: str | None = None,
    setup_only: bool = False,
) -> int:
    if config and resume:
        raise ValueError("--config 与 --resume 不能同时指定")
    model = AgentModelSettings.from_environment().resolve("interaction-agent")
    binary, home = ensure_profile(root)
    selected = resume
    if not setup_only:
        client, _ = ensure_workbench(port, root)
        bridge = ResearchBridge(client, root, selected)
        if config:
            asyncio.run(bridge.create_config(load_config(config.resolve())))
            selected = bridge.run_id
        elif selected:
            asyncio.run(bridge.status())
    patch = write_profile_patch(
        root,
        Path.cwd(),
        model.model,
        port=port,
        run_id=selected,
        destination=root / "runtime/tui" / new_id("frontend") / "entry.patch.yml",
    )
    if setup_only:
        print(f"DSH TUI plugin 已准备：{home / 'profiles' / PROFILE}")
        return 0
    token = secrets.token_urlsafe(32)
    proxy = GatewayProxy(model.base_url, model.api_key, access_token=token)
    command = [str(binary), "--profile", PROFILE, "--patch", str(patch)]
    if session:
        command += ["--resume", session]
    environment = {
        **os.environ,
        "DSH_HOME": str(home),
        "DSH_TELEMETRY_DISABLED": "1",
        "DSH_TELEMETRY_MODE": "DISABLED",
        "DSH_TUI_LANG": "zh",
        "DEEPSEEK_BASE_URL": proxy.url,
        "DEEPSEEK_API_KEY": token,
    }
    previous = signal.getsignal(signal.SIGINT)
    child: subprocess.Popen[bytes] | None = None
    try:
        child = subprocess.Popen(command, cwd=Path.cwd(), env=environment)
        signal.signal(signal.SIGINT, lambda *_: None)
        return child.wait()
    finally:
        signal.signal(signal.SIGINT, previous)
        if child is not None and child.poll() is None:
            child.terminate()
            child.wait(timeout=10)
        proxy.close()
        if os.environ.get("RSI_TUI_AUDIT") == "1":
            (patch.parent / "gateway-audit.json").write_text(
                json.dumps(proxy.requests, ensure_ascii=False)
            )
        print(
            f"研究工作台继续运行：http://127.0.0.1:{port}；研究 ID：{selected or '可在工作台查看'}"
        )
