"""Freeze and independently recheck best kernels; no model calls or new exploration."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import time
from pathlib import Path

from demo import control_request

from areal_rsi.base.storage import write_json


def finish(manifest: dict, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            result = control_request(manifest, {"operation": "finish", "manifest": manifest})
            write_json(
                output / "progress.json", {"stage": result["stage"], "message": result["stage"]}
            )
            if result["status"] == "completed":
                candidate = output / "candidate"
                candidate.mkdir(exist_ok=True)
                for task, chosen in result["selected"].items():
                    source = base64.b64decode(chosen["source"], validate=True)
                    if hashlib.sha256(source).hexdigest() != chosen["sha256"]:
                        raise ValueError("最终源码摘要不匹配")
                    folder = candidate / task
                    folder.mkdir(exist_ok=True)
                    (folder / "model_new.py").write_bytes(source)
                write_json(output / "final-selection.json", result)
                write_json(
                    output / "feedback.json", {"status": "completed", "tasks": result["receipts"]}
                )
                return
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            write_json(output / "progress.json", {"stage": "连接重试", "message": str(exc)[:300]})
        time.sleep(5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    finish(json.loads(args.manifest.read_text()), args.output)
