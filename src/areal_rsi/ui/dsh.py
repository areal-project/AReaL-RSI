"""薄启动器：组合现成 DSH TUI 与 RSI MCP，不实现聊天或 Agent loop。"""

from __future__ import annotations

import json
import os
import signal
import subprocess
from pathlib import Path

from areal_rsi.config import AgentModelSettings
from areal_rsi.modules.dsh_config import write_profile_patch
from areal_rsi.modules.dsh_profile import PROFILE, ensure_profile
from areal_rsi.modules.gateway import GatewayProxy


def launch_tui(root: Path, *, resume: str | None = None, setup_only: bool = False) -> int:
    model_config = AgentModelSettings.from_environment().resolve("frontend")
    binary, home = ensure_profile(root)
    patch = write_profile_patch(root, Path.cwd(), model_config.model)
    if setup_only:
        print(f"DSH TUI profile 已准备：{home / 'profiles' / PROFILE}")
        return 0
    proxy = GatewayProxy(model_config.base_url, model_config.api_key)
    command = [
        str(binary),
        "--profile",
        PROFILE,
        "--patch",
        str(patch),
    ]
    if resume:
        command += ["--resume", resume]
    environment = {
        **os.environ,
        "DSH_HOME": str(home),
        "DSH_TELEMETRY_DISABLED": "1",
        "DSH_TELEMETRY_MODE": "DISABLED",
        "DSH_TUI_LANG": "zh",
        "DEEPSEEK_BASE_URL": proxy.url,
        "DEEPSEEK_API_KEY": "local-proxy",
    }
    previous = signal.getsignal(signal.SIGINT)
    child: subprocess.Popen[bytes] | None = None
    try:
        child = subprocess.Popen(command, cwd=Path.cwd(), env=environment)
        # DSH 直接接管终端；Ctrl+C 交给它处理，Python 不重新实现输入行为。
        signal.signal(signal.SIGINT, lambda *_: None)
        return child.wait()
    finally:
        signal.signal(signal.SIGINT, previous)
        if child is not None and child.poll() is None:
            child.terminate()
            child.wait(timeout=10)
        proxy.close()
        if os.environ.get("RSI_TUI_AUDIT") == "1":
            # 显式诊断开关：含对话/工具原文，仅写入本地，不含代理持有的 API Key。
            (root.resolve() / "tui-gateway-audit.json").write_text(
                json.dumps(proxy.requests, ensure_ascii=False)
            )
