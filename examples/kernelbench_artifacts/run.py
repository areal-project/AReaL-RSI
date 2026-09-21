"""Launch the repository-owned KernelBench research workflow."""

from __future__ import annotations

import argparse
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

from areal_rsi.base.storage import new_id, safe_name, write_json
from areal_rsi.ui.launch import ensure_workbench


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Launch a KernelBench single-task pass@k run")
    parser.add_argument("--port", type=int, default=8197)
    parser.add_argument(
        "--data-root", type=Path, help="data directory for a new service; must match when reused"
    )
    parser.add_argument(
        "--environment", type=Path, default=Path(__file__).with_name("et15.json")
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "check the research environment without registering a run or using model/GPU resources"
        ),
    )
    parser.add_argument(
        "--resume", help="resume a run with its existing sessions and submission quota"
    )
    parser.add_argument(
        "--web-research",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="allow web research by default; use --no-web-research for an offline baseline",
    )
    args = parser.parse_args(argv)
    try:
        if args.resume and args.web_research is not None:
            raise ValueError("A resumed run keeps its original web-research setting")
        prepare = runpy.run_path(str(Path(__file__).with_name("demo.py")))["prepare"]
        if args.check:
            print(
                json.dumps(
                    prepare(Path("."), args.environment.resolve(), "preflight", check=True),
                    ensure_ascii=False,
                )
            )
            return 0
        client, info = ensure_workbench(args.port, args.data_root)
        print(f"Workbench: {client.url}", flush=True)
        if "configured-runs" not in info.get("capabilities", []):
            raise ValueError("The running workbench does not support configured runs; restart it")
        root = Path(info["data_root"])
        rid = safe_name(args.resume) if args.resume else new_id("research_kernelbench")
        print(
            f"Run ID: {rid}; resume with examples/kernelbench_artifacts/run.py --resume {rid}",
            flush=True,
        )
        existing = (root / "runs" / rid / "run.json").exists()
        if not existing:
            environment = root / "runs" / rid / "setup/environment.json"
            if args.resume and not environment.exists():
                raise ValueError("No launch record exists for this run; check the --resume ID")
            if not environment.exists():
                write_json(environment, json.loads(args.environment.read_text()), immutable=True)
            config = prepare(root, environment, rid, web_research=args.web_research)
            settings = {name: config[name] for name in ("base_url", "model", "reasoning_effort")}
            settings["api_key"] = os.environ.get(config.get("api_key_env", "RSI_API_KEY"), "")
            client.request(
                "/api/runs", {"run_id": rid, "config": config, "model_settings": settings}
            )
        detail = client.request("/api/runs/" + rid)
        if (detail.get("finalization") or {}).get("status") == "completed":
            print("This run has completed final verification; omit --resume for a new run.")
        elif detail["status"] == "planned":
            client.request(f"/api/runs/{rid}/start", {})
        elif not detail.get("worker_alive") or detail["status"] == "paused":
            client.request(f"/api/runs/{rid}/resume", {})
        print(f"Run page: {client.url}/?run={rid}")
        print(f"Run directory: {root / 'runs' / rid}")
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"Launch failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
