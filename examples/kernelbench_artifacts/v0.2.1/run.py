"""Reproduce the v0.2.1 three-study batch using the shared KernelBench adapter."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import runpy
import sys
import tempfile
from collections.abc import Iterator, Mapping
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from areal_rsi.base.storage import new_id, safe_name, write_json
from areal_rsi.ui.launch import WorkbenchClient, ensure_workbench

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[2]


@contextmanager
def environment_scope(values: Mapping[str, str]) -> Iterator[None]:
    """Pin one preparation process without importing the retired Workflow config."""
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def check_source(path: Path = HERE / "source-lock.json") -> None:
    """Do not silently run a later prompt, scheduler, or evaluator under this version."""
    lock = read_json(path)
    changed = [
        name
        for name, digest in lock["files"].items()
        if not (REPOSITORY / name).is_file()
        or hashlib.sha256((REPOSITORY / name).read_bytes()).hexdigest() != digest
    ]
    if changed:
        raise ValueError("代码与 v0.2.1 不一致，请使用保存此示例的代码版本：" + ", ".join(changed))
    for package, expected in lock["packages"].items():
        if importlib.metadata.version(package) != expected:
            raise ValueError(f"{package} 需要 {expected}；请执行 uv sync --locked")


def prepare_trial(item: tuple[dict[str, Any], str, bool]) -> dict[str, Any]:
    batch, task, check = item
    recipe = batch["experiment"]
    model = recipe["models"][task]
    minutes = recipe["prepare"]["max_effective_seconds"] / 60
    root = Path(batch["data_root"])
    row = batch["runs"][task]
    if not check and (root / "runs" / row["run_id"] / "run.json").exists():
        return {"task": task, "registered": True}
    prepare = runpy.run_path(str(HERE.parent / "demo.py"))["prepare"]
    # Separate processes: each task pins a different model through the existing adapter.
    with environment_scope(
        {
            "RSI_BACKEND": recipe["backend"],
            "RSI_BASE_URL": recipe["base_url"],
            "RSI_MODEL": model,
            "RSI_REASONING_EFFORT": recipe["reasoning_effort"],
        }
    ):
        config = prepare(
            root,
            Path(batch["environment_file"]),
            row["run_id"],
            check=check,
            check_credentials=False,
            task=task,
            name=f"{task} · {'Luna' if 'luna' in model else 'Astra'} · 有效时间 {minutes:g} 分钟",
            **recipe["prepare"],
        )
    if check:
        return {"task": task, "preflight": config}
    write_json(Path(row["config_file"]), config, immutable=True)
    manifest = read_json(root / "runs" / row["run_id"] / "setup/gpuq-manifest.json")
    return {
        "task": task,
        "model": config["model"],
        "reference_ms": manifest["tasks"][task]["reference_calibration"]["ref_ms"],
    }


def register_and_start(batch: dict[str, Any], client: WorkbenchClient, key: str) -> None:
    root = Path(batch["data_root"])
    rows = list(batch["runs"].values())
    for row in rows:
        if not (root / "runs" / row["run_id"] / "run.json").exists():
            config = read_json(Path(row["config_file"]))
            settings = {
                field: config[field]
                for field in ("backend", "base_url", "model", "reasoning_effort")
            }
            # The workbench saves this in the run's private credentials file, never batch.json.
            settings["api_key"] = key
            client.request(
                "/api/runs", {"run_id": row["run_id"], "config": config, "model_settings": settings}
            )
    members = [row["run_id"] for row in rows]
    records = [r for r in client.request("/api/runs")["runs"] if r["run_id"] in members]
    group = {
        "id": batch["batch_id"],
        "name": batch["experiment"]["group_name"],
        "created_at": min(r["created_at"] for r in records),
        "run_ids": members,
    }
    path = root / "workbench-groups.json"
    groups = read_json(path)["groups"] if path.exists() else []
    write_json(path, {"groups": [group, *(g for g in groups if g["id"] != group["id"])]})

    def start(row: dict[str, str]) -> dict[str, str]:
        worker = WorkbenchClient(batch["port"])
        worker.token = client.token
        url = "/api/runs/" + row["run_id"]
        detail = worker.request(url)
        if detail["status"] == "planned":
            detail = worker.request(url + "/start", {})
        # Re-running this launcher does not resume paused/stopped/completed research.
        return {
            "run_id": row["run_id"],
            "status": detail["status"],
            "url": worker.url + "/?run=" + row["run_id"],
        }

    with ThreadPoolExecutor(max_workers=len(rows)) as pool:
        results = list(pool.map(start, rows))
    write_json(Path(batch["directory"]) / "launch-result.json", {"group": group, "runs": results})
    for result in results:
        print(f"{result['status']}: {result['url']}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="v0.2.1：三题并行，四名研究员各 30 分钟有效时间")
    parser.add_argument(
        "--source-lock", type=Path, default=HERE / "source-lock.json",
        help="经审查的独立源码快照；默认仍验证原版本，不自动更新或跳过校验",
    )
    parser.add_argument("--check", action="store_true", help="只检查代码与远端环境，不启动模型/GPU")
    parser.add_argument("--port", type=int, default=8199)
    parser.add_argument("--data-root", type=Path, default=REPOSITORY / ".rsi/kernelbench-v0.2.1")
    parser.add_argument("--ssh-host", help="连接同一环境的本机 SSH 别名，默认 et15")
    parser.add_argument(
        "--credential-file", type=Path, help='本机 JSON 文件，内容为 {"api_key":"…"}'
    )
    parser.add_argument("--resume", help="重试本脚本输出的 batch ID，不新建研究或重置预算")
    args = parser.parse_args(argv)
    check_source(args.source_lock)
    root = args.data_root.expanduser().resolve()
    batch_id = safe_name(args.resume) if args.resume else new_id("v021")
    directory = root / "operator" / batch_id
    if args.resume:
        batch = read_json(directory / "batch.json")
        saved_lock = directory / "source-lock.json"
        if read_json(saved_lock) != read_json(args.source_lock):
            raise ValueError("重试须使用该批次保存的源码锁")
        if batch["data_root"] != str(root) or batch["port"] != args.port:
            raise ValueError("重试时请沿用原 data-root 和 port")
        if args.ssh_host:
            raise ValueError("重试沿用已保存的 SSH 配置，无需再传 --ssh-host")
        if batch["experiment"] != read_json(HERE / "experiment.json"):
            raise ValueError("重试时实验配置不能改变")
    else:
        batch = {
            "batch_id": batch_id,
            "directory": str(directory),
            "data_root": str(root),
            "port": args.port,
            "experiment": read_json(HERE / "experiment.json"),
            "environment_file": str(directory / "environment.json"),
            "runs": {},
        }
        for task in batch["experiment"]["models"]:
            batch["runs"][task] = {
                "run_id": f"research_{batch_id}_{task.lower()}",
                "config_file": str(directory / f"{task}-config.json"),
            }
    if args.check:
        environment = read_json(
            Path(batch["environment_file"]) if args.resume else HERE / "environment.json"
        )
        if args.ssh_host:
            environment["host"] = args.ssh_host
        with tempfile.TemporaryDirectory(prefix="rsi-v021-check-") as temporary:
            batch["environment_file"] = str(Path(temporary) / "environment.json")
            write_json(Path(batch["environment_file"]), environment)
            with ProcessPoolExecutor(max_workers=3) as pool:
                results = list(
                    pool.map(prepare_trial, [(batch, task, True) for task in batch["runs"]])
                )
            for result in results:
                print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0

    key = (
        read_json(args.credential_file.expanduser())["api_key"]
        if args.credential_file
        else os.environ.get("RSI_API_KEY", "")
    ).strip()
    if not key:
        raise ValueError("请设置 RSI_API_KEY 或传入 --credential-file；密钥不写入示例")
    client, _ = ensure_workbench(args.port, root)
    if not args.resume:
        environment = read_json(HERE / "environment.json")
        if args.ssh_host:
            environment["host"] = args.ssh_host
        write_json(Path(batch["environment_file"]), environment, immutable=True)
        write_json(directory / "batch.json", batch, immutable=True)
        write_json(
            directory / "source-lock.json", read_json(args.source_lock), immutable=True
        )
    print(f"批次：{batch_id}；中断后使用相同参数加 --resume {batch_id}", flush=True)
    print(f"工作台：{client.url}；正在准备三题并校准 reference…", flush=True)
    items = [(batch, task, False) for task in batch["runs"]]
    with ProcessPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(prepare_trial, items))
    write_json(directory / "preparation.json", results)
    # Only start researchers after all three preparations/calibrations have succeeded.
    register_and_start(batch, client, key)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from None
