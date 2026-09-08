"""KernelBench manifests, fixed-target generation and complete-set aggregation.

GPU evaluation is a separate Python 3.10 program in examples/kernelbench.
Research Agents edit harness snapshots; only this trusted target executor
produces kernels for search/final evaluation.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REVISION = "423217d9fda91e0c2d67e4a43bf62f96f6d104f1"
SCHEMA = "kernelbench-rsi-v1"
SPLITS = ("development", "search", "final")


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def prepare_dataset(
    repository: Path,
    destination: Path,
    selections: Mapping[str, Sequence[Mapping[str, int]]],
    *,
    revision: str = REVISION,
    dataset_kind: str = "kernelbench",
) -> dict[str, Any]:
    """Extract selected source blobs from a pinned Git tree, without importing torch."""
    if destination.exists():
        raise FileExistsError("Choose a fresh immutable dataset directory")
    if set(selections) != set(SPLITS) or any(not selections[s] for s in SPLITS):
        raise ValueError("Specify nonempty development, search and final task lists")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("KernelBench revision must be a full Git commit SHA")
    if dataset_kind not in {"kernelbench", "synthetic"} or (
        dataset_kind == "kernelbench" and revision != REVISION
    ):
        raise ValueError("Real data must use the supported pinned KernelBench revision")
    seen: set[tuple[int, int]] = set()
    manifests: dict[str, dict[str, Any]] = {}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        staged = Path(temporary) / "prepared"
        staged.mkdir()
        for split in SPLITS:
            directory = staged / (split if split == "development" else "private/" + split)
            rows = []
            for selection in selections[split]:
                if set(selection) != {"level", "problem_id"}:
                    raise ValueError("Task selections accept only level and problem_id")
                level, problem = selection["level"], selection["problem_id"]
                if type(level) is not int or type(problem) is not int or min(level, problem) < 1:
                    raise ValueError("Task level and problem_id must be positive integers")
                if (level, problem) in seen:
                    raise ValueError("Task pools must be disjoint and have no duplicate tasks")
                seen.add((level, problem))
                prefix = f"KernelBench/level{level}"
                listing = subprocess.run(
                    [
                        "git",
                        "-C",
                        str(repository),
                        "ls-tree",
                        "--name-only",
                        f"{revision}:{prefix}",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.splitlines()
                matches = [name for name in listing if re.fullmatch(rf"{problem}_[^/]+\.py", name)]
                if len(matches) != 1:
                    raise ValueError(
                        f"Expected exactly one pinned file for level {level} problem {problem}"
                    )
                source = subprocess.run(
                    ["git", "-C", str(repository), "show", f"{revision}:{prefix}/{matches[0]}"],
                    check=True,
                    capture_output=True,
                ).stdout
                task_id = f"level{level}_{problem}"
                relative = f"references/{task_id}.py"
                path = directory / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(source)
                rows.append(
                    {
                        "task_id": task_id,
                        "level": level,
                        "problem_id": problem,
                        "reference": relative,
                        "reference_sha256": _sha(source),
                    }
                )
            manifest = {
                "schema": SCHEMA,
                "revision": revision,
                "split": split,
                "dataset_kind": dataset_kind,
                "tasks": rows,
            }
            manifest["manifest_id"] = _sha(json.dumps(manifest, sort_keys=True).encode())
            manifests[split] = manifest
            _write(directory / "manifest.json", manifest)
        protocol = {
            "schema": SCHEMA,
            "revision": revision,
            "dataset_kind": dataset_kind,
            "splits": {
                s: {"manifest_id": manifests[s]["manifest_id"], "count": len(manifests[s]["tasks"])}
                for s in SPLITS
            },
        }
        _write(staged / "protocol.json", protocol)
        staged.rename(destination)
    return protocol


def read_manifest(path: Path) -> dict[str, Any]:
    body = json.loads(path.read_text())
    if body.get("schema") != SCHEMA or body.get("split") not in SPLITS:
        raise ValueError("Unknown KernelBench manifest")
    identity = body.get("manifest_id")
    expected = _sha(
        json.dumps({k: v for k, v in body.items() if k != "manifest_id"}, sort_keys=True).encode()
    )
    if identity != expected:
        raise ValueError("Manifest identity mismatch")
    tasks = body.get("tasks")
    if not isinstance(tasks, list) or not tasks or len({r["task_id"] for r in tasks}) != len(tasks):
        raise ValueError("Manifest must contain distinct tasks")
    for row in tasks:
        if not re.fullmatch(r"level[1-9][0-9]*_[1-9][0-9]*", row["task_id"]):
            raise ValueError("Invalid task ID")
        relative = Path(row["reference"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Reference path must remain inside the manifest directory")
        source = path.parent / relative
        if source.is_symlink() or not source.resolve().is_relative_to(path.parent.resolve()):
            raise ValueError("Reference path escapes its task pool")
        if _sha(source.read_bytes()) != row["reference_sha256"]:
            raise ValueError("Reference content changed after preparation")
    return body  # type: ignore[no-any-return]


def harness_identity(directory: Path) -> str:
    from areal_rsi.modules.harness import validate_harness

    validate_harness(directory)
    files = {
        p.relative_to(directory).as_posix(): _sha(p.read_bytes())
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }
    return _sha(json.dumps(files, sort_keys=True).encode())


def check_edit_scope(base: Path, candidate: Path, allowed: str) -> str:
    """Enforce skill-only edits by actual file contents, independently of prompts."""
    identity = harness_identity(candidate)
    harness_identity(base)
    if allowed not in {"skills", "harness"}:
        raise ValueError("Allowed edit scope must be skills or harness")
    if allowed == "skills":

        def fixed(directory: Path) -> dict[str, bytes]:
            return {
                p.relative_to(directory).as_posix(): p.read_bytes()
                for p in directory.rglob("*")
                if p.is_file() and p.relative_to(directory).parts[0] != "skills"
            }

        if fixed(base) != fixed(candidate):
            raise ValueError("Candidate changed files outside the user-authorized skills scope")
    return identity


async def generate(
    manifest_path: Path,
    harness: Path,
    destination: Path,
    *,
    max_model_requests: int = 8,
    max_output_tokens: int = 8192,
    timeout_seconds: float = 600,
    runtime_root: Path | None = None,
    expected_target_model: str | None = None,
) -> dict[str, Any]:
    """Operator entry: call ONLY RSI_TARGET credentials, exporting target-written files.

    Keep search/final destinations private. The RA consumes development traces
    and aggregate search scores, never writes the kernels passed to VALIDATE.
    """
    from areal_rsi.api.base import Task
    from areal_rsi.base.storage import (
        FileContentStore,
        FileTrajectoryStore,
        FileVersionManager,
        encode,
    )
    from areal_rsi.config import ModelConfig
    from areal_rsi.modules.runtime import DshAgentRuntime
    from areal_rsi.modules.task_execution import FileTaskExecutor, TargetGateway, TaskExportError

    if destination.exists():
        raise FileExistsError("Each target evaluation requires a fresh private output directory")
    if min(max_model_requests, max_output_tokens, timeout_seconds) <= 0:
        raise ValueError("Target budgets must be positive")
    manifest = read_manifest(manifest_path)
    identity = harness_identity(harness)
    target = ModelConfig.from_environment("target")
    if expected_target_model is not None and target.model != expected_target_model:
        raise ValueError("RSI_TARGET_MODEL differs from the operator's pinned target model")
    destination.mkdir(parents=True)
    content = FileContentStore(destination / "content")
    trajectories = FileTrajectoryStore(destination / "trajectories", content)
    versions = FileVersionManager(destination / "versions")
    model = await versions.create_model(
        target.model, await content.put_json(target.public_identity())
    )
    version = await versions.create_harness("kernelbench-harness", await content.snapshot(harness))
    solution = await versions.create_solution(model, version)
    records = []
    for row in manifest["tasks"]:
        task_id = row["task_id"]
        gateway = TargetGateway(
            target.base_url,
            target.api_key,
            model=target.model,
            max_requests=max_model_requests,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
        )
        runtime = DshAgentRuntime(
            destination / "sessions" / task_id,
            content,
            trajectories,
            base_url=gateway.url,
            api_key=gateway.token,
            timeout=timeout_seconds,
            max_tokens=max_output_tokens,
            dsh_root=runtime_root or destination / "dsh",
        )
        task_directory = destination / "tasks" / task_id
        task_directory.mkdir(parents=True)
        reference = (manifest_path.parent / row["reference"]).read_text()
        task = Task(
            task_id,
            {
                "instruction": (
                    "Implement ModelNew for this PyTorch reference. Write the complete candidate "
                    "to model_new.py in the workspace. Do not return code only in chat. Use the "
                    "configured harness and task budget. The reference's get_inputs and "
                    "get_init_inputs define the workload."
                ),
                "reference_source": reference,
            },
        )
        record: dict[str, Any] = {
            "task_id": task_id,
            "producer": "task-agent",
            "target": target.public_identity(),
            "harness_id": identity,
        }
        try:
            exported = await FileTaskExecutor(
                runtime, content, destination / "execution" / task_id
            ).run(
                solution,
                task,
                exports=("model_new.py",),
                timeout_seconds=timeout_seconds,
            )
            record["trajectory"] = encode(exported.result.trajectory)
            record["output"] = encode(exported.result.output)
            ref = exported.files.get("model_new.py")
            if ref is None:
                record["status"] = "no_output"
            else:
                code = await content.read_bytes(ref)
                (task_directory / "model_new.py").write_bytes(code)
                record.update(status="generated", kernel_sha256=_sha(code))
        except TaskExportError:
            record["status"] = "invalid_kernel"
        except TimeoutError:
            record["status"] = "budget_exhausted"
        except Exception as exc:
            record["status"] = "budget_exhausted" if gateway.exhausted else "infra_error"
            # Avoid exporting exception strings containing provider connection
            # details. Full local runtime traces remain in the private job tree.
            record["error"] = type(exc).__name__
        finally:
            record["model_requests"] = gateway.calls
            await asyncio.to_thread(gateway.close)
        _write(task_directory / "generation.json", record)
        records.append(record)
    summary = {
        "schema": SCHEMA,
        "manifest_id": manifest["manifest_id"],
        "split": manifest["split"],
        "target": target.public_identity(),
        "harness_id": identity,
        "budget": {
            "max_model_requests": max_model_requests,
            "max_output_tokens_per_request": max_output_tokens,
            "timeout_seconds": timeout_seconds,
        },
        "tasks": records,
    }
    _write(destination / "generation.json", summary)
    return summary


@dataclass(frozen=True)
class Aggregate:
    status: str
    fast_1: float | None
    correctness: float | None
    compiled: float | None
    task_count: int
    infra_errors: int
    simulated: bool
    manifest_id: str
    protocol_id: str
    harness_id: str
    target_model: str


def aggregate_results(
    manifest: Mapping[str, Any],
    results: Sequence[Mapping[str, Any]],
) -> Aggregate:
    """All selected tasks stay in the denominator; infrastructure errors invalidate scores."""
    ids = [row["task_id"] for row in manifest["tasks"]]
    if (
        not ids
        or len(ids) != len(set(ids))
        or len(results) != len(ids)
        or {r["task_id"] for r in results} != set(ids)
    ):
        raise ValueError("Evaluation must contain exactly one result for every selected task")
    identity = results[0]
    keys = ("protocol_id", "harness_id", "target_model", "simulated")
    if any(any(row.get(key) != identity.get(key) for key in keys) for row in results):
        raise ValueError(
            "Results from different protocols, targets, harnesses or simulation modes cannot mix"
        )
    if any(row.get("manifest_id") != manifest["manifest_id"] for row in results):
        raise ValueError("Result belongs to another task pool")
    if (
        any(not isinstance(identity.get(k), str) or not identity[k] for k in keys[:3])
        or type(identity.get("simulated")) is not bool
    ):
        raise ValueError("Result identities and an explicit simulation flag are required")
    errors, correct, compiled, fast = 0, 0, 0, 0
    for row in results:
        status = row.get("status")
        if status == "infra_error":
            errors += 1
            continue
        if status not in {"evaluated", "no_output", "budget_exhausted", "invalid_kernel"}:
            raise ValueError("Unknown kernel result status")
        if type(row.get("compiled")) is not bool or type(row.get("correctness")) is not bool:
            raise ValueError("Completed task results require explicit boolean outcomes")
        if row["correctness"] and (not row["compiled"] or status != "evaluated"):
            raise ValueError("Invalid correctness claim")
        compiled += row["compiled"]
        if row["correctness"]:
            timings = []
            for value in (row.get("runtime_ms"), row.get("reference_ms")):
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int | float)
                    or not math.isfinite(value)
                    or value <= 0
                ):
                    raise ValueError("A correct timed kernel requires finite positive runtimes")
                timings.append(float(value))
            correct += 1
            fast += timings[1] > timings[0]
    count = len(ids)
    return Aggregate(
        "incomplete" if errors else "completed",
        None if errors else fast / count,
        None if errors else correct / count,
        None if errors else compiled / count,
        count,
        errors,
        identity["simulated"],
        manifest["manifest_id"],
        identity["protocol_id"],
        identity["harness_id"],
        identity["target_model"],
    )


def mock_results(
    manifest: Mapping[str, Any],
    *,
    harness_id: str,
    target_model: str = "simulated-target",
    outcomes: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Deterministic GPU substitute. Never scores a real GPU or implies improvement."""
    results = []
    for row in manifest["tasks"]:
        task_id = row["task_id"]
        outcome = (outcomes or {}).get(task_id, "correct_fast")
        if outcome not in {
            "correct_fast",
            "correct_slow",
            "wrong",
            "compile_error",
            "infra_error",
            "no_output",
        }:
            raise ValueError("Unknown mock outcome")
        results.append(
            {
                "task_id": task_id,
                "manifest_id": manifest["manifest_id"],
                "protocol_id": "simulated-kernelbench-v1",
                "harness_id": harness_id,
                "target_model": target_model,
                "simulated": True,
                "status": outcome if outcome in {"infra_error", "no_output"} else "evaluated",
                "compiled": outcome in {"correct_fast", "correct_slow", "wrong"},
                "correctness": outcome in {"correct_fast", "correct_slow"},
                "runtime_ms": 0.5 if outcome == "correct_fast" else 2.0,
                "reference_ms": 1.0,
            }
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--repository", type=Path, required=True)
    prepare.add_argument("--config", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    target = sub.add_parser("generate")
    target.add_argument("--manifest", type=Path, required=True)
    target.add_argument("--harness", type=Path, required=True)
    target.add_argument("--output", type=Path, required=True)
    target.add_argument("--max-model-requests", type=int, default=8)
    target.add_argument("--max-output-tokens", type=int, default=8192)
    target.add_argument("--timeout", type=float, default=600)
    aggregate = sub.add_parser("aggregate")
    aggregate.add_argument("--manifest", type=Path, required=True)
    aggregate.add_argument("--results", type=Path, required=True)
    aggregate.add_argument("--output", type=Path, required=True)
    mock = sub.add_parser("mock")
    mock.add_argument("--manifest", type=Path, required=True)
    mock.add_argument("--harness-id", required=True)
    mock.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        config = json.loads(args.config.read_text())
        prepare_dataset(
            args.repository, args.output, config["tasks"], revision=config.get("revision", REVISION)
        )
    elif args.command == "generate":
        asyncio.run(
            generate(
                args.manifest,
                args.harness,
                args.output,
                max_model_requests=args.max_model_requests,
                max_output_tokens=args.max_output_tokens,
                timeout_seconds=args.timeout,
            )
        )
    elif args.command == "mock":
        manifest = read_manifest(args.manifest)
        _write(args.output, mock_results(manifest, harness_id=args.harness_id))
    else:
        result = aggregate_results(
            read_manifest(args.manifest), json.loads(args.results.read_text())
        )
        _write(args.output, asdict(result))
        print(json.dumps(asdict(result)))


if __name__ == "__main__":
    main()
