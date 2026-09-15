"""Repository launcher: one backend for browser use and scripted experiments."""

from __future__ import annotations

import argparse
import json
import os
import runpy
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from areal_rsi.base.storage import new_id, safe_name, write_json

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
        if value.get("api_version") != 2:
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
        if root is not None and Path(info["data_root"]).resolve() != root.resolve():
            raise ValueError(
                "已有 RSI 工作台使用另一个数据目录；省略 --data-root 复用它，或指定其他端口"
            )
        return client, info
    directory = (root or Path(os.environ.get("RSI_DATA_ROOT") or ".rsi/research-lab")).resolve()
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
    parser = argparse.ArgumentParser(description="启动 RSI 工作台或 KernelBench 八题 demo")
    parser.add_argument("--type", choices=("web", "demo"), default="web")
    parser.add_argument("--port", type=int, default=8197)
    parser.add_argument("--data-root", type=Path, help="新服务的数据目录；复用时必须一致")
    parser.add_argument(
        "--environment", type=Path, default=REPOSITORY / "examples/kernelbench_artifacts/et15.json"
    )
    parser.add_argument(
        "--check", action="store_true", help="仅检查 demo 环境，不注册任务或运行模型/GPU"
    )
    parser.add_argument("--resume", help="继续同一个 demo，保留会话和提交额度")
    parser.add_argument(
        "--web-research",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="demo 新研究默认允许联网调研；--no-web-research 用于不联网的 baseline 对照",
    )
    args = parser.parse_args(argv)
    try:
        if args.type != "demo" and (args.check or args.resume or args.web_research is not None):
            raise ValueError("--check / --resume / --[no-]web-research 仅用于 --type demo")
        if args.resume and args.web_research is not None:
            raise ValueError("恢复研究沿用原联网设置；修改条件请新建研究")
        prepare = (
            runpy.run_path(str(REPOSITORY / "examples/kernelbench_artifacts/demo.py"))["prepare"]
            if args.type == "demo"
            else None
        )
        if args.check:
            assert prepare is not None
            print(
                json.dumps(
                    prepare(Path("."), args.environment.resolve(), "preflight", check=True),
                    ensure_ascii=False,
                )
            )
            return 0
        client, info = ensure_workbench(args.port, args.data_root)
        print(f"工作台：{client.url}", flush=True)
        if args.type == "web":
            return 0
        if "configured-runs" not in info.get("capabilities", []):
            raise ValueError("已有工作台不支持配置启动，请使用当前版本重启")
        root = Path(info["data_root"])
        rid = safe_name(args.resume) if args.resume else new_id("research_kernelbench")
        print(f"研究 ID：{rid}；中断后用 --type demo --resume {rid} 继续", flush=True)
        existing = (root / "runs" / rid / "run.json").exists()
        if not existing:
            assert prepare is not None
            environment = root / "runs" / rid / "setup/environment.json"
            if args.resume and not environment.exists():
                raise ValueError("找不到这个 run 的启动记录；请核对 --resume 的研究 ID")
            if not environment.exists():
                write_json(environment, json.loads(args.environment.read_text()), immutable=True)
            config = prepare(root, environment, rid, web_research=args.web_research)
            # A reused service may have different default credentials from this shell.
            settings = {name: config[name] for name in ("base_url", "model", "reasoning_effort")}
            settings["api_key"] = os.environ.get(config.get("api_key_env", "RSI_API_KEY"), "")
            client.request(
                "/api/runs", {"run_id": rid, "config": config, "model_settings": settings}
            )
        detail = client.request("/api/runs/" + rid)
        if (detail.get("finalization") or {}).get("status") == "completed":
            print("该研究已完成最终复测；新实验请省略 --resume。")
        elif detail["status"] == "planned":
            client.request(f"/api/runs/{rid}/start", {})
        elif not detail.get("worker_alive") or detail["status"] == "paused":
            client.request(f"/api/runs/{rid}/resume", {})
        print(f"研究页面：{client.url}/?run={rid}")
        print(f"记录目录：{root / 'runs' / rid}")
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
