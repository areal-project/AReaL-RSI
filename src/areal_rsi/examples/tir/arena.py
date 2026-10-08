"""TIR dataset registration, trajectory collection and isolated Arena scoring."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any

from areal_rsi.api.control import decode, json_bytes, read_record, safe_name
from areal_rsi.api.execution import (
    ArenaDatasetResources,
    ArenaJobLaunch,
    ArenaResourceKind,
    ArenaResourcePin,
    ArenaTrainingDataset,
)
from areal_rsi.infra.storage.files import write_bytes

if TYPE_CHECKING:
    from areal_rsi.examples.tir.synthesis import SyntheticMathCase


def synthetic_candidates(
    *, seed: int, count: int, exclude_task_ids: frozenset[str] = frozenset()
) -> tuple[SyntheticMathCase, ...]:
    from areal_rsi.examples.tir.synthesis import synthesize_tir_math_cases

    pool = synthesize_tir_math_cases(seed=seed, count=count * 4 if exclude_task_ids else count)
    quota = count // 8
    families: Counter[str] = Counter()
    selected = []
    for candidate in pool:
        if (
            candidate.case.task_id not in exclude_task_ids
            and families[candidate.family.value] < quota
        ):
            selected.append(candidate)
            families[candidate.family.value] += 1
    if len(selected) != count:
        raise ValueError("Not enough disjoint synthetic cases; choose another seed")
    return tuple(selected)


def collect() -> None:
    trajectory = Path(os.environ["ARENA_TRAJECTORY_PATH"])
    events = [
        decode(dict[str, Any], json.loads(line))
        for line in trajectory.read_bytes().splitlines()
        if line.strip()
    ]
    finals = [event for event in events if event.get("type") == "result"]
    if len(finals) != 1:
        raise ValueError("Claude trajectory must contain one final result")
    final = finals[0]
    if final.get("is_error") is not False or not isinstance(final.get("result"), str):
        raise ValueError("Claude did not finish successfully")
    output = Path(os.environ["ARENA_AGENT_OUTPUT_DIR"])
    output.mkdir(parents=True, exist_ok=True)
    (output / "answer.json").write_bytes(
        json_bytes(
            {
                "response": final["result"],
                "usage": final.get("usage", {}),
                "subtype": final.get("subtype"),
                "task_id": os.environ["ARENA_TASK_ID"],
            }
        )
    )


def boxed_fraction(response: str) -> Fraction | None:
    """Accept a final numeric box, with exact rational arithmetic and no eval()."""
    starts = list(re.finditer(r"\\boxed\s*\{", response))
    if not starts:
        return None
    start = end = starts[-1].end()
    depth = 1
    while end < len(response) and depth:
        if response[end] == "{":
            depth += 1
        elif response[end] == "}":
            depth -= 1
        end += 1
    if depth:
        return None
    text = response[start : end - 1].strip().replace(" ", "").replace(",", "")
    fraction = re.fullmatch(r"\\(?:d?frac)\{([+-]?\d+)\}\{([+-]?\d+)\}", text)
    if fraction:
        text = f"{fraction[1]}/{fraction[2]}"
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d+)?|\d+/[+-]?\d+)", text):
        return None
    try:
        return Fraction(text)
    except (ValueError, ZeroDivisionError):
        return None


def freeze_math_verifier(destination: Path) -> None:
    """Bundle the real historical verifier's pure Python dependencies for Arena."""
    from importlib.metadata import distribution
    from io import BytesIO
    from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

    versions: dict[str, str] = {}
    archive = BytesIO()
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as bundle:
        for name in (
            "math-verify",
            "latex2sympy2_extended",
            "sympy",
            "mpmath",
            "antlr4-python3-runtime",
        ):
            package = distribution(name)
            versions[name] = package.version
            for relative in sorted(package.files or (), key=str):
                if (
                    ".." in relative.parts
                    or "__pycache__" in relative.parts
                    or "tests" in relative.parts
                    or relative.suffix == ".pyc"
                ):
                    continue
                source = Path(str(package.locate_file(relative)))
                if source.is_symlink() or not source.is_file():
                    raise ValueError("Verifier dependency must be a regular file")
                info = ZipInfo(str(relative), date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                bundle.writestr(info, source.read_bytes())
    destination.mkdir(parents=True, exist_ok=True)
    write_bytes(destination / "dependencies.zip", archive.getvalue(), immutable=True)
    write_bytes(destination / "versions.json", json_bytes(versions), immutable=True)


def reward(*, use_math_verify: bool = False) -> None:
    oracle_path = Path(os.environ["ARENA_ORACLE_PATH"])
    # Arena materializes a file URI as a directory containing the original file.
    # Keep this platform boundary explicit; ambiguous or missing oracles fail.
    if oracle_path.is_dir():
        files = [path for path in oracle_path.rglob("*") if path.is_file()]
        if len(files) != 1:
            raise ValueError("Arena oracle mount must contain exactly one file")
        oracle_path = files[0]
    oracle = read_record(oracle_path.resolve(), dict[str, str])
    source = Path(os.environ["ARENA_AGENT_OUTPUT_DIR"]) / "answer.json"
    answer = read_record(source, dict[str, Any])
    response = decode(str, answer["response"])
    value = boxed_fraction(response)
    score = float(value is not None and value == Fraction(oracle["answer"]))
    verifier = "exact-rational-box"
    if use_math_verify:
        from importlib.metadata import version

        from areal_rsi.examples.tir.claude_solver import _claude_math_reward

        # Use the historical scorer unchanged; parser/grader failures propagate.
        score = _claude_math_reward(response, oracle["answer"])
        verifier = "claude-boxed-math-verify-" + version("math-verify")
    output = Path(os.environ["ARENA_REWARD_OUTPUT_FILE"])
    output.parent.mkdir(parents=True, exist_ok=True)
    copied = output.parent / "answer.json"
    shutil.copyfile(source, copied)
    output.write_bytes(
        json_bytes(
            {
                "status": "OK",
                "score": score,
                "raw": {
                    "verifier_status": "OK",
                    "verifier": verifier,
                    "agent_status": "OK",
                    "answer_format_valid": value is not None,
                    "parsed_answer": str(value) if value is not None else None,
                    "expected_answer": oracle["answer"],
                    "task_id": answer["task_id"],
                    "usage": answer["usage"],
                },
                "artifacts": [
                    {"name": "answer.json", "path": str(copied), "type": "application/json"}
                ],
            }
        )
    )


def register_math_reward(resources: ArenaDatasetResources, output: Path) -> ArenaDatasetResources:
    """Keep the registered questions and switch to the frozen historical TIR scorer."""
    from areal_rsi.infra.coordination.arena import (
        arena_get,
        publish_resource,
        published_pin,
        resource_version,
        upload_asset,
    )

    manifest = output / "resources.json"
    frozen_input = output / "input-resources.json"
    if manifest.exists():
        if read_record(frozen_input, ArenaDatasetResources) != resources:
            raise ValueError("Math verifier registration changed; use a new output directory")
        saved = read_record(manifest, ArenaDatasetResources)
        for pin in (saved.reward, saved.benchmark):
            if published_pin(pin.kind, pin.key, pin.version) != pin:
                raise ValueError("Registered math verifier changed")
        return saved
    output.mkdir(parents=True, exist_ok=True)
    write_bytes(frozen_input, json_bytes(resources), immutable=True)
    dependencies = output / "dependencies"
    freeze_math_verifier(dependencies)
    deps_uri = upload_asset(dependencies, receipt=output / "dependency-upload.json")
    runtime = output / "runtime"
    if not runtime.exists():
        runtime.mkdir()
        shutil.copytree(
            Path(__file__).resolve().parents[2],
            runtime / "areal_rsi",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    runtime_uri = upload_asset(runtime, receipt=output / "runtime-upload.json")
    previous = arena_get(f"/rewards/{resources.reward.key}/versions/{resources.reward.version}")
    if previous is None or previous["content_hash"] != resources.reward.content_hash:
        raise ValueError("Source Arena reward changed")
    spec = previous["spec"]
    spec["files"] = [
        {"uri": runtime_uri, "dest": "runtime"},
        {"uri": deps_uri, "dest": "dependencies"},
    ]
    spec["entrypoint"] = (
        'PYTHONPATH="$ARENA_REWARD_DIR/dependencies/dependencies.zip:$ARENA_REWARD_DIR/runtime" '
        "python3 -m areal_rsi.examples.tir.arena reward-math"
    )
    reward_pin = publish_resource(
        ArenaResourceKind.REWARD,
        "rsi-tir-math-verify",
        resource_version(spec),
        spec,
        description="Historical strict TIR verifier with immutable math-verify dependencies",
    )
    benchmark_spec = {
        "reward_ref": reward_pin.ref(),
        "case_refs": [{"case_ref": pin.ref(), "weight": 1} for pin in resources.cases],
        "aggregation": {"method": "mean", "failed_task_policy": "failed_as_zero"},
    }
    benchmark = publish_resource(
        ArenaResourceKind.BENCHMARK,
        resources.benchmark.key,
        resource_version(benchmark_spec),
        benchmark_spec,
        description="Registered TIR questions with the unchanged historical strict verifier",
    )
    saved = replace(resources, reward=reward_pin, benchmark=benchmark)
    write_bytes(manifest, json_bytes(saved), immutable=True)
    return saved


def register_synthetic(
    output: Path,
    model: ArenaResourcePin,
    *,
    run_id: str,
    seed: int,
    count: int,
    harness: ArenaResourcePin | None = None,
    exclude_task_ids: frozenset[str] = frozenset(),
) -> ArenaDatasetResources:
    from areal_rsi.infra.coordination.arena import (
        asset_digest,
        publish_resource,
        published_pin,
        resource_version,
        upload_asset,
    )

    safe_name(run_id)
    candidates = synthetic_candidates(seed=seed, count=count, exclude_task_ids=exclude_task_ids)
    harness = harness or published_pin(
        ArenaResourceKind.HARNESS, "claude-code-with-skills", "5.0.2"
    )
    manifest = output / "resources.json"
    if manifest.exists():
        saved = read_record(manifest, ArenaDatasetResources)
        if (
            saved.run_id != run_id
            or saved.model != model
            or saved.harness != harness
            or tuple(pin.key for pin in saved.cases)
            != tuple("rsi-tir-" + item.case.task_id[:24] for item in candidates)
        ):
            raise ValueError("Arena dataset registration changed; use a new output directory")
        for pin in (
            saved.harness,
            saved.model,
            saved.env,
            saved.reward,
            *saved.cases,
            saved.benchmark,
        ):
            if published_pin(pin.kind, pin.key, pin.version) != pin:
                raise ValueError("Published Arena resource differs from its frozen registration")
        return saved
    output.mkdir(parents=True, exist_ok=True)
    for pin in (harness, model):
        if published_pin(pin.kind, pin.key, pin.version) != pin:
            raise ValueError("Arena model or Harness pin changed")
    assets = output / "assets"
    assets.mkdir(exist_ok=True)
    bundle = assets / "workspace"
    bundle.mkdir(exist_ok=True)
    write_bytes(
        bundle / "README.md",
        b"TIR mathematics workspace. Use tools to solve the query.\n",
        immutable=True,
    )
    for candidate in candidates:
        case = candidate.case
        write_bytes(
            assets / f"{case.task_id}.md",
            (
                "Solve the following mathematics problem. You may use Bash and Python as a "
                "calculator. Give your final answer as an exact number in \\boxed{answer}.\n\n"
                + case.question
                + "\n"
            ).encode(),
            immutable=True,
        )
        write_bytes(
            assets / f"{case.task_id}.json", json_bytes({"answer": case.answer}), immutable=True
        )
    uri = upload_asset(assets, receipt=output / "dataset-upload.json")
    package_root = Path(__file__).resolve().parents[2]
    snapshot = output / "runtime"
    if not snapshot.exists():
        with tempfile.TemporaryDirectory(dir=output, prefix=".source-") as temporary:
            staged = Path(temporary) / "src"
            staged.mkdir()
            shutil.copytree(
                package_root,
                staged / "areal_rsi",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            staged.rename(snapshot)
    runtime_uri = upload_asset(snapshot, receipt=output / "runtime-upload.json")
    source_digest = asset_digest(snapshot)
    env_spec = {
        "runtime": {"kind": "files"},
        "timeout_seconds": 600,
        "files": [{"uri": runtime_uri, "dest": "runtime"}],
        "hooks": {
            "collect_script": 'PYTHONPATH="$ARENA_ENV_DIR/runtime" python3 -m '
            "areal_rsi.examples.tir.arena collect"
        },
        "timeouts": {"setup_seconds": 60, "agent_seconds": 600, "collect_seconds": 60},
    }
    env = publish_resource(
        ArenaResourceKind.ENV,
        "rsi-tir-math-files",
        resource_version(
            {
                "source_digest": source_digest,
                "spec": env_spec,
            }
        ),
        env_spec,
        description="TIR Agent sandbox with immutable source and trajectory collection",
    )
    reward_spec = {
        "runtime": {
            "kind": "image",
            "image": "asr.antgroup-inc.cn/arcaarealagent/gameagent@sha256:"
            "e752f7df6d437ad7ce1328d71ee23e066d4ff5afbe6776d6b0ee4e229ea5fd90",
            "command": "sleep infinity",
            "profile": "image_networked",
        },
        "evaluation_mode": "separate",
        "files": [{"uri": runtime_uri, "dest": "runtime"}],
        "entrypoint": 'PYTHONPATH="$ARENA_REWARD_DIR/runtime" python3 -m '
        "areal_rsi.examples.tir.arena reward",
        "normalization": ".score",
        "accepted_oracle_schema": "rsi.tir.rational.v1",
        "ttl_seconds": 600,
        "resource_spec": {"cpu": "1", "memory": "2Gi", "disk": "10Gi"},
        "io_contract": {
            "output_file": "$ARENA_REWARD_OUTPUT_FILE",
            "artifact_globs": ["$ARENA_REWARD_OUTPUT_DIR/**/*"],
        },
    }
    reward_pin = publish_resource(
        ArenaResourceKind.REWARD,
        "rsi-tir-rational-reward",
        resource_version({"source_digest": source_digest, "spec": reward_spec}),
        reward_spec,
        description="Isolated exact rational TIR scoring",
    )

    def publish_case(candidate: SyntheticMathCase) -> ArenaResourcePin:
        case = candidate.case
        spec = {
            "env_ref": env.ref(),
            "files": [{"uri": runtime_uri, "dest": "runtime"}],
            "query": {"uri": uri + case.task_id + ".md", "media_type": "text/markdown"},
            "bundle": {
                "uri": uri + "workspace/",
                "media_type": "application/vnd.envarena.directory",
            },
            "oracle": {"uri": uri + case.task_id + ".json", "media_type": "application/json"},
            "oracle_schema": "rsi.tir.rational.v1",
            "metadata": {
                "source_task_id": case.task_id,
                "family": candidate.family.value,
                "seed": str(seed),
                "purpose": "synthetic-calibration",
            },
        }
        return publish_resource(
            ArenaResourceKind.CASE,
            "rsi-tir-" + case.task_id[:24],
            resource_version(spec),
            spec,
            description="Generated TIR mathematics case; oracle isolated from Agent",
        )

    # Each task owns a different entity; publication remains sequential per entity.
    with ThreadPoolExecutor(max_workers=8) as workers:
        pins = tuple(workers.map(publish_case, candidates))
    benchmark_spec = {
        "reward_ref": reward_pin.ref(),
        "case_refs": [{"case_ref": pin.ref(), "weight": 1} for pin in pins],
        "aggregation": {"method": "mean", "failed_task_policy": "failed_as_zero"},
    }
    benchmark = publish_resource(
        ArenaResourceKind.BENCHMARK,
        run_id,
        resource_version(benchmark_spec),
        benchmark_spec,
        description=f"TIR synthetic calibration: seed={seed}, count={count}",
    )
    resources = ArenaDatasetResources(
        run_id, harness, model, env, reward_pin, tuple(pins), benchmark
    )
    write_bytes(manifest, json_bytes(resources), immutable=True)
    return resources


def register_training(
    resources: ArenaDatasetResources, output: Path, *, run_id: str
) -> ArenaTrainingDataset:
    """Streams currently select Env identities; preserve Case data in legacy Env projections."""
    from areal_rsi.infra.coordination.arena import (
        arena_get,
        arena_json,
        publish_resource,
        published_pin,
        resource_version,
    )

    safe_name(run_id)
    manifest = output / "training-resources.json"
    frozen_input = output / "input-resources.json"
    if manifest.exists():
        if (
            not frozen_input.exists()
            or read_record(frozen_input, ArenaDatasetResources) != resources
        ):
            raise ValueError(
                "Arena training input differs or is missing; use a new output directory"
            )
        saved = read_record(manifest, ArenaTrainingDataset)
        if (
            saved.run_id != run_id
            or saved.model != resources.model
            or saved.harness != resources.harness
            or saved.reward != resources.reward
        ):
            raise ValueError("Arena training registration changed; use a new output directory")
        for pin in (saved.model, saved.harness, saved.reward, *saved.envs):
            if published_pin(pin.kind, pin.key, pin.version) != pin:
                raise ValueError("Arena training resource changed")
        return saved
    output.mkdir(parents=True, exist_ok=True)
    write_bytes(frozen_input, json_bytes(resources), immutable=True)
    env = arena_get(f"/envs/{resources.env.key}/versions/{resources.env.version}")
    if env is None:
        raise ValueError("Arena execution environment is missing")

    def publish_training_env(pin: ArenaResourcePin) -> ArenaResourcePin:
        case = arena_get(f"/cases/{pin.key}/versions/{pin.version}")
        if case is None or case["content_hash"] != pin.content_hash:
            raise ValueError("Arena training case changed")
        spec = {
            **env["spec"],
            **{
                key: case["spec"][key]
                for key in (
                    "query",
                    "bundle",
                    "oracle",
                    "oracle_schema",
                    "metadata",
                    "files",
                )
            },
        }
        spec["metadata"] = {**spec["metadata"], "task": run_id, "purpose": "grpo-training"}
        return publish_resource(
            ArenaResourceKind.ENV,
            run_id + "-" + pin.key.removeprefix("rsi-tir-"),
            resource_version(spec),
            spec,
            description="TIR training row for the production Env-based Stream selector",
        )

    with ThreadPoolExecutor(max_workers=8) as workers:
        envs = tuple(workers.map(publish_training_env, resources.cases))
    stream = arena_get(f"/streams/{run_id}")
    stream = stream.get("stream", stream) if stream is not None else None
    if stream is None:
        arena_json(
            "stream",
            "create",
            "--stream-id",
            run_id,
            "--task",
            run_id,
            "--harness",
            f"{resources.harness.key}@{resources.harness.version}",
            "--reward",
            f"{resources.reward.key}@{resources.reward.version}",
        )
    else:
        if stream.get("status") != "ACTIVE" or stream.get("task") != run_id:
            raise ValueError("Arena training Stream is unavailable or refers to another task")
        if (
            stream.get("default_harness_ref") != resources.harness.ref()
            or stream.get("default_reward_ref") != resources.reward.ref()
        ):
            arena_json(
                "stream",
                "update",
                run_id,
                "--harness",
                f"{resources.harness.key}@{resources.harness.version}",
                "--reward",
                f"{resources.reward.key}@{resources.reward.version}",
            )
    dataset = decode(
        dict[str, Any], arena_json("stream", "dataset", run_id, "--limit", "1000")["data"]
    )
    selected = {item["data_id"] for item in dataset["items"]}
    expected = {f"env:{pin.key}@{pin.version}" for pin in envs}
    if selected != expected or dataset["total"] != len(envs):
        raise ValueError("Arena Stream selection differs from the pinned training dataset")
    registered = ArenaTrainingDataset(
        run_id, run_id, resources.model, resources.harness, resources.reward, tuple(envs)
    )
    write_bytes(manifest, json_bytes(registered), immutable=True)
    return registered


@dataclass(frozen=True, slots=True)
class ArenaCalibrationSelection:
    job_id: str
    report_digest: str
    seed: int
    probe_cases: int
    incomplete_cases: int
    twice_solved_cases: int
    selected_task_ids: tuple[str, ...]
    selected_pass_counts: tuple[int, ...]
    zero_passes: int
    one_pass: int


def select_calibrated(
    resources: ArenaDatasetResources,
    job_report: Path,
    output: Path,
    *,
    run_id: str,
    seed: int,
    count: int,
) -> ArenaDatasetResources:
    """Apply the existing TIR selection policy to completed, pinned Arena probes."""
    from areal_rsi.examples.tir.synthesis import select_model_calibrated_cases
    from areal_rsi.infra.coordination.arena import publish_resource, resource_version

    safe_name(run_id)
    payload = job_report.read_bytes()
    record = decode(dict[str, Any], json.loads(payload))
    data = decode(dict[str, Any], record["data"])
    job = decode(dict[str, Any], data["job"])
    if (
        job["status"] != "DONE"
        or job["sample_numbers"] != 2
        or job["benchmark_ref"] != resources.benchmark.ref()
        or job["model_ref"] != resources.model.ref()
        or job["harness_ref"] != resources.harness.ref()
    ):
        raise ValueError("Calibration requires a completed two-sample job with the frozen refs")
    observations: dict[str, int | None] = {}
    versions = {pin.key: pin.version for pin in resources.cases}
    for item in data["result"]["per_env"]:
        pin = item["case_ref"]
        key = decode(str, pin["key"])
        if (
            key in observations
            or versions.get(key) != pin["version"]
            or item["task_count"] != 2
            or item["sample_numbers"] != 2
            or len(set(item["task_ids"])) != 2
        ):
            raise ValueError("Calibration result differs from the registered cases")
        passes = None
        if item["ok_count"] == 2 and item["failed_count"] == 0:
            score = item["score"]
            if type(score) not in (float, int) or score not in (0, 0.5, 1):
                raise ValueError("TIR calibration requires two binary reward observations")
            passes = int(score * 2)
        elif item["ok_count"] + item["failed_count"] != 2:
            raise ValueError("Calibration task counts are inconsistent")
        observations[key] = passes
    if set(observations) != set(versions):
        raise ValueError("Calibration result is missing registered cases")
    candidates = synthetic_candidates(seed=seed, count=len(resources.cases))
    keys = tuple("rsi-tir-" + item.case.task_id[:24] for item in candidates)
    if set(keys) != set(versions):
        raise ValueError("Calibration seed does not reproduce the registered questions")
    successes = tuple(observations[key] for key in keys)
    selected = select_model_calibrated_cases(candidates, successes, count=count, seed=seed)
    selected_ids = tuple(item.case.task_id for item in selected.cases)
    selected_keys = tuple("rsi-tir-" + task_id[:24] for task_id in selected_ids)
    by_key = {pin.key: pin for pin in resources.cases}
    cases = tuple(by_key[key] for key in selected_keys)
    summary = ArenaCalibrationSelection(
        decode(str, job["job_id"]),
        "sha256:" + hashlib.sha256(payload).hexdigest(),
        seed,
        len(candidates),
        successes.count(None),
        successes.count(2),
        selected_ids,
        tuple(decode(int, observations[key]) for key in selected_keys),
        selected.zero_passes,
        selected.one_pass,
    )
    output.mkdir(parents=True, exist_ok=True)
    write_bytes(output / "input-resources.json", json_bytes(resources), immutable=True)
    write_bytes(output / "probe-job.json", payload, immutable=True)
    write_bytes(output / "selection.json", json_bytes(summary), immutable=True)
    spec = {
        "reward_ref": resources.reward.ref(),
        "case_refs": [{"case_ref": pin.ref(), "weight": 1} for pin in cases],
        "aggregation": {"method": "mean", "failed_task_policy": "failed_as_zero"},
    }
    benchmark = publish_resource(
        ArenaResourceKind.BENCHMARK,
        run_id,
        resource_version(spec),
        spec,
        description="TIR training cases selected by the historical two-sample calibration policy",
    )
    saved = replace(resources, run_id=run_id, cases=cases, benchmark=benchmark)
    write_bytes(output / "resources.json", json_bytes(saved), immutable=True)
    return saved


def main() -> None:
    parser = argparse.ArgumentParser(description="TIR tasks in Arena; Slurm training stays local")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("collect")
    sub.add_parser("reward")
    sub.add_parser("reward-math")
    register = sub.add_parser("register")
    register.add_argument("--output", type=Path, required=True)
    register.add_argument("--model-pin", type=Path, required=True)
    register.add_argument("--harness-pin", type=Path)
    register.add_argument("--exclude-task-ids", type=Path)
    register.add_argument("--run-id", required=True)
    register.add_argument("--seed", type=int, default=20261002)
    register.add_argument("--count", type=int, default=1024)
    launch = sub.add_parser("launch")
    launch.add_argument("--resources", type=Path, required=True)
    launch.add_argument("--samples", type=int, default=2)
    launch.add_argument("--concurrency", type=int, default=4)
    launch.add_argument("--receipt", type=Path, required=True)
    launch.add_argument("--adopt-job")
    training = sub.add_parser("register-training")
    training.add_argument("--resources", type=Path, required=True)
    training.add_argument("--output", type=Path, required=True)
    training.add_argument("--run-id", required=True)
    select = sub.add_parser("select")
    select.add_argument("--resources", type=Path, required=True)
    select.add_argument("--job-report", type=Path, required=True)
    select.add_argument("--output", type=Path, required=True)
    select.add_argument("--run-id", required=True)
    select.add_argument("--seed", type=int, default=20261002)
    select.add_argument("--count", type=int, default=512)
    math_reward = sub.add_parser("register-math-reward")
    math_reward.add_argument("--resources", type=Path, required=True)
    math_reward.add_argument("--output", type=Path, required=True)
    bind = sub.add_parser("bind")
    bind.add_argument("--resources", type=Path, required=True)
    bind.add_argument("--model-pin", type=Path)
    bind.add_argument("--harness-pin", type=Path)
    bind.add_argument("--reward-pin", type=Path)
    bind.add_argument("--run-id", required=True)
    bind.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "collect":
        collect()
    elif args.command in ("reward", "reward-math"):
        reward(use_math_verify=args.command == "reward-math")
    elif args.command == "register":
        resources = register_synthetic(
            args.output,
            read_record(args.model_pin, ArenaResourcePin),
            run_id=args.run_id,
            seed=args.seed,
            count=args.count,
            harness=read_record(args.harness_pin, ArenaResourcePin) if args.harness_pin else None,
            exclude_task_ids=(
                frozenset(read_record(args.exclude_task_ids, tuple[str, ...]))
                if args.exclude_task_ids
                else frozenset()
            ),
        )
        print(json_bytes(resources).decode())
    elif args.command == "launch":
        from areal_rsi.infra.coordination.arena import launch_job

        resources = read_record(args.resources, ArenaDatasetResources)
        result = launch_job(
            ArenaJobLaunch(
                resources.run_id,
                resources.benchmark,
                resources.harness,
                resources.model,
                args.samples,
                args.concurrency,
            ),
            args.receipt,
            adopt_job=args.adopt_job,
        )
        print(json_bytes(result).decode())
    elif args.command == "register-training":
        training_resources = register_training(
            read_record(args.resources, ArenaDatasetResources), args.output, run_id=args.run_id
        )
        print(json_bytes(training_resources).decode())
    elif args.command == "select":
        registered = select_calibrated(
            read_record(args.resources, ArenaDatasetResources),
            args.job_report,
            args.output,
            run_id=args.run_id,
            seed=args.seed,
            count=args.count,
        )
        print(json_bytes(registered).decode())
    elif args.command == "register-math-reward":
        registered = register_math_reward(
            read_record(args.resources, ArenaDatasetResources), args.output
        )
        print(json_bytes(registered).decode())
    elif args.command == "bind":
        from areal_rsi.infra.coordination.arena import (
            publish_resource,
            published_pin,
            resource_version,
        )

        resources = read_record(args.resources, ArenaDatasetResources)
        model = read_record(args.model_pin, ArenaResourcePin) if args.model_pin else resources.model
        harness = (
            read_record(args.harness_pin, ArenaResourcePin)
            if args.harness_pin
            else resources.harness
        )
        reward_pin = (
            read_record(args.reward_pin, ArenaResourcePin) if args.reward_pin else resources.reward
        )
        for pin in (model, harness, reward_pin):
            if published_pin(pin.kind, pin.key, pin.version) != pin:
                raise ValueError("Changed model, Harness or Reward is not published")
        benchmark = resources.benchmark
        if reward_pin != resources.reward:
            spec = {
                "reward_ref": reward_pin.ref(),
                "case_refs": [{"case_ref": pin.ref(), "weight": 1} for pin in resources.cases],
                "aggregation": {"method": "mean", "failed_task_policy": "failed_as_zero"},
            }
            benchmark = publish_resource(
                ArenaResourceKind.BENCHMARK,
                benchmark.key,
                resource_version(spec),
                spec,
                description="TIR questions with an explicitly frozen Reward version",
            )
        write_bytes(
            args.output,
            json_bytes(
                replace(
                    resources,
                    model=model,
                    harness=harness,
                    reward=reward_pin,
                    benchmark=benchmark,
                    run_id=safe_name(args.run_id),
                )
            ),
            immutable=True,
        )


if __name__ == "__main__":
    main()
