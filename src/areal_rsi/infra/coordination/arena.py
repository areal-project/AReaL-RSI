"""Arena CLI boundaries and immutable registry publication shared by task profiles."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from areal_rsi.api.control import decode, json_bytes, read_record
from areal_rsi.api.execution import (
    ArenaAssetUpload,
    ArenaJobLaunch,
    ArenaJobReceipt,
    ArenaJobStatus,
    ArenaResourceKind,
    ArenaResourcePin,
    ArenaTaskStatus,
)
from areal_rsi.infra.storage.files import write_bytes


class ArenaError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def arena_json(
    *arguments: str, body: object | None = None, timeout_seconds: int = 180
) -> dict[str, Any]:
    """Use the installed CLI profile; credentials never become process arguments."""
    result = subprocess.run(
        [os.environ.get("ARENA_CLI", "arena"), *arguments, "--json"],
        input=json_bytes(body) if body is not None else None,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    try:
        envelope = decode(dict[str, Any], json.loads(result.stdout))
    except (ValueError, json.JSONDecodeError) as error:
        raise ArenaError("InvalidResponse", "Arena CLI did not return a JSON envelope") from error
    if result.returncode or envelope.get("ok") is not True:
        failure = decode(dict[str, Any], envelope.get("error", {}))
        raise ArenaError(str(failure.get("code", "CLIError")), str(failure.get("message", "")))
    return envelope


def arena_get(path: str) -> dict[str, Any] | None:
    try:
        return decode(dict[str, Any], arena_json("api", "get", path)["data"])
    except ArenaError as error:
        if error.code != "NotFound":
            raise
        return None


def _contains(actual: Any, expected: Any) -> bool:
    # The API adds defaults to specs. Compare every supplied field, allowing only
    # omission of empty optional collections by the server's JSON encoder.
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(_contains(a, e) for a, e in zip(actual, expected, strict=True))
        )
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        for key, value in expected.items():
            received = actual.get(key, {} if isinstance(value, dict) else [])
            # Registry masks secret-named fields, including *_OUTPUT_TOKENS.
            # Their values cannot be verified by GET; include the original spec
            # in resource_version() and verify model calls in an actual Task.
            if (
                isinstance(received, str)
                and "****" in received
                and any(word in key.lower() for word in ("token", "key", "secret", "password"))
            ):
                continue
            if not _contains(received, value):
                return False
        return True
    return bool(actual == expected)


def published_pin(kind: ArenaResourceKind, key: str, version: str) -> ArenaResourcePin:
    value = arena_get(f"/{kind}/{key}/versions/{version}")
    if value is None or value.get("status") != "PUBLISHED":
        raise ValueError(f"Arena resource is not published: {kind}/{key}@{version}")
    return decode(
        ArenaResourcePin,
        {"kind": kind, "key": key, "version": version, "content_hash": value["content_hash"]},
    )


def verify_pins(pins: tuple[ArenaResourcePin, ...]) -> None:
    """Check every immutable reference with bounded parallel, read-only requests."""
    with ThreadPoolExecutor(max_workers=16) as workers:
        actual = tuple(workers.map(lambda pin: published_pin(pin.kind, pin.key, pin.version), pins))
    if actual != pins:
        raise ValueError("Published Arena resources differ from their frozen references")


def publish_resource(
    kind: ArenaResourceKind,
    key: str,
    version: str,
    spec: dict[str, Any],
    *,
    description: str,
) -> ArenaResourcePin:
    """Read before every mutation; refuse edits or reuse of incompatible versions."""
    prefix = f"/{kind}/{key}"
    entity = arena_get(prefix)
    current = arena_get(f"{prefix}/versions/{version}") if entity is not None else None
    if current is not None:
        if not _contains(current["spec"], spec):
            raise ValueError(f"Arena spec changed; choose a new version: {key}@{version}")
        if current["status"] not in {"DRAFT", "PUBLISHED"}:
            raise ValueError(f"Arena version is unavailable: {key}@{version}")
    else:
        if entity is None:
            arena_json(
                "api",
                "post",
                f"/{kind}",
                "--data",
                "@-",
                body={
                    "key": key,
                    "name": key,
                    "description": description,
                    "tags": ["source:areal-rsi"],
                },
            )
        arena_json(
            "api",
            "post",
            f"{prefix}/versions",
            "--data",
            "@-",
            body={"version": version, "storage_ref": "default", "spec": spec},
        )
    if current is None or current["status"] == "DRAFT":
        arena_json("api", "post", f"{prefix}/versions/{version}/publish", "--data", "@-", body={})
    pin = published_pin(kind, key, version)
    readback = arena_get(f"{prefix}/versions/{version}")
    if readback is None or not _contains(readback["spec"], spec):
        raise ValueError(
            f"Published Arena spec differs from the requested resource: {key}@{version}"
        )
    return pin


def resource_version(identity: object) -> str:
    """Changed model, Harness, task data or settings imply a new immutable version."""
    return "1.0.0-" + hashlib.sha256(json_bytes(identity)).hexdigest()[:16]


def asset_digest(path: Path) -> str:
    if path.is_symlink():
        raise ValueError("Arena upload source must not contain symbolic links")
    files = sorted(path.rglob("*")) if path.is_dir() else [path]
    identity: dict[str, str] = {}
    for file in files:
        if file.is_symlink():
            raise ValueError("Arena upload source must not contain symbolic links")
        if file.is_file():
            digest = hashlib.sha256()
            with file.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            name = str(file.relative_to(path)) if path.is_dir() else file.name
            identity[name] = digest.hexdigest()
    if not identity:
        raise ValueError("Arena upload source is empty")
    return "sha256:" + hashlib.sha256(json_bytes(identity)).hexdigest()


def upload_asset(path: Path, *, receipt: Path | None = None) -> str:
    digest = asset_digest(path)
    if receipt is not None and receipt.exists():
        saved = read_record(receipt, ArenaAssetUpload)
        if saved.source_digest != digest:
            raise ValueError("Arena upload source changed; use a new output directory")
        return saved.uri
    value = decode(
        dict[str, Any],
        arena_json("upload", str(path), "--storage", "default", timeout_seconds=1800)["data"],
    )
    uri = decode(str, value["uri"])
    if receipt is not None:
        write_bytes(receipt, json_bytes(ArenaAssetUpload(digest, uri)), immutable=True)
    return uri


def job_status(job_id: str) -> ArenaJobStatus:
    value = arena_get(f"/jobs/{job_id}")
    if value is None:
        raise ValueError(f"Arena job is missing: {job_id}")
    value = decode(dict[str, Any], value.get("job", value))
    tasks: list[ArenaTaskStatus] = []
    offset = 0
    while True:
        page = arena_get(f"/jobs/{job_id}/tasks?limit=200&offset={offset}")
        if page is None:
            raise ValueError("Arena task page is missing")
        items = decode(list[dict[str, Any]], page["items"])
        tasks.extend(
            decode(ArenaTaskStatus, {"task_id": item["task_id"], "status": item["status"]})
            for item in items
        )
        offset += len(items)
        if offset >= page["total"]:
            break
        if not items:
            raise ValueError("Arena task pagination ended before the reported total")
    return decode(
        ArenaJobStatus,
        {
            "job_id": job_id,
            "status": value["status"],
            "tasks": [{"task_id": task.task_id, "status": task.status} for task in tasks],
        },
    )


def launch_job(
    launch: ArenaJobLaunch, receipt: Path, *, adopt_job: str | None = None
) -> ArenaJobReceipt:
    """Persist intent before launch; uncertain responses require explicit reconciliation."""
    digest = "sha256:" + hashlib.sha256(json_bytes(launch)).hexdigest()
    if receipt.exists():
        saved = read_record(receipt, ArenaJobReceipt)
        if saved.launch_digest != digest or (adopt_job and saved.job_id != adopt_job):
            raise ValueError("Arena launch changed; use a new receipt path")
        return saved
    intent = receipt.with_suffix(".intent.json")
    if intent.exists():
        if read_record(intent, ArenaJobLaunch) != launch:
            raise ValueError("Arena launch differs from its persisted intent")
        if not adopt_job:
            raise ValueError("Launch outcome is uncertain; inspect Arena and pass --adopt-job")
    else:
        verify_pins((launch.benchmark, launch.harness, launch.model))
        write_bytes(intent, json_bytes(launch), immutable=True)
    if adopt_job:
        job_id = adopt_job
    else:
        created = arena_json(
            "job",
            "create",
            "--file",
            "-",
            body={
                "job_id": launch.run_id,
                "benchmark_ref": launch.benchmark.ref(),
                "harness_ref": launch.harness.ref(),
                "model_ref": launch.model.ref(),
                "sample_numbers": launch.samples,
                "concurrency_policy": {"max_parallel": launch.concurrency, "max_retries": 0},
            },
        )
        job_id = decode(str, created["data"]["job_id"])
    value = arena_get(f"/jobs/{job_id}")
    if value is None:
        raise ValueError("Arena launch did not produce a job")
    job = value.get("job", value)
    if any(
        job.get(key) != expected
        for key, expected in {
            "benchmark_ref": launch.benchmark.ref(),
            "harness_ref": launch.harness.ref(),
            "model_ref": launch.model.ref(),
            "sample_numbers": launch.samples,
        }.items()
    ):
        raise ValueError("Arena job differs from the persisted launch")
    saved = ArenaJobReceipt(digest, job_id)
    write_bytes(receipt, json_bytes(saved), immutable=True)
    return saved


def sync_model(
    source: Path, key: str, route: dict[str, Any], *, gateway_url: str
) -> ArenaResourcePin:
    """A changed checkpoint gets a distinct gateway alias and registry version."""
    from areal_rsi.infra.storage.artifacts import validate_hf_model_files

    validate_hf_model_files(source)
    token = os.environ.get("ARENA_LLM_API_KEY", "")
    if not token:
        raise ValueError("Load ARENA_LLM_API_KEY from the private credentials file")
    digest = asset_digest(source)
    alias = key + "-" + digest[7:23]
    metadata = {**route.get("metadata", {}), "source_digest": digest}
    requested = {**route, "model_name": alias, "metadata": metadata}
    current = arena_get(f"/llm/models/{alias}")
    if current is None:
        arena_json("llm-model", "create", "--file", "-", body=requested)
    elif not _contains(current.get("model", current), requested):
        raise ValueError("Gateway alias already has a different deployment; choose another key")
    env = {
        "ANTHROPIC_BASE_URL": gateway_url.rstrip("/"),
        "ANTHROPIC_AUTH_TOKEN": token,
        "ANTHROPIC_API_KEY": token,
        "ANTHROPIC_MODEL": alias,
        "OPENAI_BASE_URL": gateway_url.rstrip("/"),
        "OPENAI_API_KEY": token,
        "OPENAI_MODEL": alias,
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "512",
    }
    for size in ("HAIKU", "SONNET", "OPUS"):
        env[f"ANTHROPIC_DEFAULT_{size}_MODEL"] = alias
    spec = {"model_name": alias, "env": env, "parameters": {"temperature": 0.6}}
    return publish_resource(
        ArenaResourceKind.MODEL,
        key,
        resource_version({"source_digest": digest, "spec": spec}),
        spec,
        description="Versioned Slurm model served through the Arena LLM Gateway",
    )


def sync_harness(source: Path, key: str, spec: dict[str, Any], output: Path) -> ArenaResourcePin:
    digest = asset_digest(source)
    uri = upload_asset(source, receipt=output.with_suffix(".upload.json"))
    requested = {**spec, "runtime": {**spec["runtime"], "uri": uri}}
    return publish_resource(
        ArenaResourceKind.HARNESS,
        key,
        resource_version({"source_digest": digest, "spec": requested}),
        requested,
        description="Versioned Agent executable and Harness settings",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    model = sub.add_parser("sync-model")
    model.add_argument("--route", type=Path, required=True)
    model.add_argument("--gateway-url", default="https://arenaonline-prod.alipay.com/api")
    harness = sub.add_parser("sync-harness")
    harness.add_argument("--spec", type=Path, required=True)
    for command in (model, harness):
        command.add_argument("--source", type=Path, required=True)
        command.add_argument("--key", required=True)
        command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "sync-model":
        pin = sync_model(
            args.source,
            args.key,
            read_record(args.route, dict[str, Any]),
            gateway_url=args.gateway_url,
        )
    else:
        pin = sync_harness(
            args.source, args.key, read_record(args.spec, dict[str, Any]), args.output
        )
    write_bytes(args.output, json_bytes(pin), immutable=True)
    print(json_bytes(pin).decode())


if __name__ == "__main__":
    main()
