"""Repository launcher: one backend for browser use and scripted experiments."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from areal_rsi.base.paths import data_root

REPOSITORY = Path(__file__).resolve().parents[3]
SERVICE = "areal-rsi"


class WorkbenchClient:
    def __init__(self, port: int):
        self.url = f"http://127.0.0.1:{port}"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.token = ""

    def request(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Content-Type": "application/json", "X-RSI-Token": self.token}
        request = urllib.request.Request(
            self.url + path,
            headers=headers,
            data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
        )
        try:
            with self.opener.open(request, timeout=60 if body is not None else 3) as response:
                value: dict[str, Any] = json.load(response)
                return value
        except urllib.error.HTTPError as exc:
            try:
                message = json.loads(exc.read()).get("error", f"HTTP {exc.code}")
            except ValueError:
                message = f"HTTP {exc.code}"
            raise ValueError(str(message)) from None

    def bootstrap(self) -> dict[str, Any]:
        value = self.request("/api/bootstrap")
        if value.get("service") != SERVICE:
            if "csrf_token" in value and "models" in value:
                raise ValueError("此端口是旧版 RSI 工作台，请先用当前代码重启服务再运行 demo")
            raise ValueError("端口已被其他服务占用")
        if value.get("api_version") != 1:
            raise ValueError("RSI 工作台 API 版本不兼容，请重启为当前版本")
        self.token = value["csrf_token"]
        return value


def ensure_workbench(port: int, root: Path | None = None) -> tuple[WorkbenchClient, dict[str, Any]]:
    if not 1 <= port <= 65535:
        raise ValueError("端口必须在 1–65535 之间")
    client = WorkbenchClient(port)
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
            occupied = False
        except OSError:
            occupied = True
    if occupied:
        try:
            info = client.bootstrap()
        except (OSError, ValueError) as exc:
            raise ValueError(f"端口 {port} 已被占用，无法复用：{exc}") from None
        if root is not None and Path(info["data_root"]).resolve() != data_root(root):
            raise ValueError(
                "已有 RSI 工作台使用另一个数据目录；省略 --data-root 复用它，或指定其他端口"
            )
        return client, info
    directory = data_root(root)
    runtime = directory / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    log = runtime / f"workbench-{port}.log"
    with log.open("ab") as stream:
        process = subprocess.Popen(
            [sys.executable, "-m", "areal_rsi", "workbench", "--port", str(port)],
            cwd=REPOSITORY,
            env={**os.environ, "RSI_DATA_ROOT": str(directory)},
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=stream,
            start_new_session=True,
        )
    (runtime / f"workbench-{port}.pid").write_text(str(process.pid) + "\n")
    until = time.monotonic() + 30
    while time.monotonic() < until:
        if process.poll() is not None:
            raise ValueError(f"工作台启动失败，请查看 {log}")
        try:
            return client, client.bootstrap()
        except (OSError, ValueError):
            time.sleep(0.2)
    process.terminate()
    raise ValueError(f"工作台启动超时，请查看 {log}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start or reuse the RSI workbench")
    parser.add_argument("--port", type=int, default=8197)
    parser.add_argument("--data-root", type=Path, help="新服务的数据目录；复用时必须一致")
    args = parser.parse_args(argv)
    try:
        client, _ = ensure_workbench(args.port, args.data_root)
        print(f"工作台：{client.url}", flush=True)
        return 0
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
