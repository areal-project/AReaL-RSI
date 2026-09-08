"""Preflight a real example and write a keyless, directly loadable ResearchConfig.

Run with the repository environment: uv run python scripts/configure_research.py --help
Describe checks trusted inputs/environments; it never runs a candidate or measures a score.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from areal_rsi.api.research import ResearchLimits, TaskBrief, VerificationContract
from areal_rsi.coordination.config import ResearchConfig, load_config
from areal_rsi.execution import ApprovedCommand

REPOSITORY = Path(__file__).resolve().parents[1]


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--example", choices=("kernelbench", "cuhkx"), required=True)
    result.add_argument("--operator", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument(
        "--target-model", help="Exact KernelBench target; must match RSI_TARGET_MODEL"
    )
    result.add_argument(
        "--target", type=float, help="Desired fast_1/accuracy in [0, 1]; omitted = unset"
    )
    result.add_argument("--seed", type=Path, help="Initial editable source directory")
    result.add_argument(
        "--instructions-file", type=Path, help="Additional user guidance as UTF-8 text"
    )
    result.add_argument("--max-agents", type=int, help="Default: KernelBench 1, CUHK-X 2")
    result.add_argument("--max-nodes", type=int, default=12)
    result.add_argument("--max-model-calls", type=int, default=200)
    result.add_argument("--max-total-tokens", type=int, default=2_000_000)
    result.add_argument("--max-verify-calls", type=int, default=6)
    result.add_argument("--max-jobs", type=int, default=1)
    result.add_argument("--max-wall-seconds", type=float, default=14400)
    result.add_argument("--base-url", default="https://4ipic.com")
    result.add_argument("--python", type=Path, default=Path(sys.executable), help="Host RSI Python")
    result.add_argument("--preflight-timeout", type=float, default=180)
    result.add_argument(
        "--allow-local", action="store_true", help="CUHK-X synthetic CPU smoke only"
    )
    return result


def absolute_path(value: Any, name: str, *, directory: bool = False) -> Path:
    if not isinstance(value, (str, Path)) or not str(value):
        raise ValueError(f"{name} must be an existing absolute path")
    path = Path(value).expanduser()
    if not path.is_absolute() or not (path.is_dir() if directory else path.is_file()):
        raise ValueError(
            f"{name} must be an existing absolute {'directory' if directory else 'file'}"
        )
    return path


def positive_seconds(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a positive finite number")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return float(value)


def gpu_ids(operator: dict[str, Any], example: str) -> tuple[str, ...]:
    if example == "kernelbench":
        runner = operator.get("gpu", {})
        if runner.get("kind") not in {"local", "ssh"}:
            raise ValueError("KernelBench requires a real local/SSH GPU runner")
        raw = runner.get("cuda_visible_devices", "")
        values = raw.split(",") if isinstance(raw, str) and raw else []
    else:
        runner = operator.get("runner", {})
        if runner.get("kind", "docker") not in {"docker", "local"}:
            raise ValueError("CUHK-X requires Docker or explicit synthetic local execution")
        devices = [
            operator.get(action, {}).get("device", runner.get("device", "cuda:0"))
            for action in ("training", "verification")
        ]
        if any(
            not isinstance(device, str) or not re.fullmatch(r"cpu|cuda:\d+", device)
            for device in devices
        ):
            raise ValueError("CUHK-X devices must be cpu or cuda:<index>")
        if all(device == "cpu" for device in devices):
            return ()
        values = runner.get("gpu_ids", [])
        if not isinstance(values, list):
            raise ValueError("runner.gpu_ids must be an explicit list")
    ids = tuple(str(value) for value in values)
    if not ids or len(set(ids)) != len(ids) or any(not re.fullmatch(r"[0-9]+", i) for i in ids):
        raise ValueError("Specify unique numeric GPU IDs in the operator configuration")
    if example == "cuhkx" and any(
        device.startswith("cuda:") and int(device.split(":")[1]) >= len(ids) for device in devices
    ):
        raise ValueError("CUHK-X CUDA device index must refer to the assigned GPU list")
    return ids


def describe(
    executable: Path,
    wrapper: Path,
    operator: Path,
    example: str,
    phase: str,
    target_model: str | None,
    ids: tuple[str, ...],
    timeout: float,
) -> dict[str, Any]:
    command = [
        str(executable),
        str(wrapper),
        "--describe" if example == "kernelbench" else "describe",
    ]
    command += ["--config", str(operator), "--phase", phase]
    if target_model:
        command += ["--target-model", target_model]
    # Describe the devices that the scheduler will reserve, not an unrelated
    # CUDA_VISIBLE_DEVICES inherited from the shell used to configure the run.
    environment = {**os.environ, "CUDA_VISIBLE_DEVICES": ",".join(ids)}
    try:
        process = subprocess.run(
            command,
            cwd=REPOSITORY,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        # Do not echo arbitrary child stderr: upstream tools can include secrets.
        raise ValueError(
            f"{example} {phase} describe preflight failed ({type(error).__name__}). "
            "Check prepared splits, Docker image or SSH/GPU setup using wrapper describe. "
            "No research configuration was written."
        ) from error
    try:
        value = json.loads(process.stdout)
    except (ValueError, TypeError) as error:
        raise ValueError(f"{example} {phase} describe did not return a JSON identity") from error
    if not isinstance(value, dict):
        raise ValueError("Describe identity must be a JSON object")
    return value


def build_config(args: argparse.Namespace) -> ResearchConfig:
    operator_path = absolute_path(args.operator, "--operator")
    executable = absolute_path(args.python, "--python")  # Preserve the venv symlink path.
    if not os.access(executable, os.X_OK):
        raise ValueError("--python is not executable")
    if args.output.exists():
        raise ValueError("Output already exists; choose a new configuration path")
    if not args.output.is_absolute():
        raise ValueError("--output must be an absolute path")
    if args.target is not None and (not math.isfinite(args.target) or not 0 <= args.target <= 1):
        raise ValueError("--target must be in [0, 1]")
    endpoint = urlsplit(args.base_url)
    if endpoint.scheme not in {"http", "https"} or not endpoint.hostname:
        raise ValueError("--base-url must be an HTTP(S) model endpoint")
    if endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
        raise ValueError("--base-url must not contain credentials, query parameters or fragments")
    positive_seconds(args.preflight_timeout, "--preflight-timeout")
    operator_bytes = operator_path.read_bytes()
    operator = json.loads(operator_bytes)
    if not isinstance(operator, dict):
        raise ValueError("Operator configuration must be a JSON object")
    prepared = absolute_path(operator.get("prepared_data"), "prepared_data", directory=True)
    example = args.example
    wrapper = REPOSITORY / "examples" / example / "run.py"
    target_model = args.target_model
    if example == "kernelbench" and (not target_model or not target_model.strip()):
        raise ValueError("KernelBench requires --target-model to pin the actual task model")
    if example == "cuhkx" and target_model:
        raise ValueError("--target-model is only used by KernelBench")
    if args.allow_local and example != "cuhkx":
        raise ValueError("--allow-local is only for CUHK-X synthetic CPU smoke")
    scope = operator.get("edit_scope", "harness") if example == "kernelbench" else "candidate"
    if example == "kernelbench" and scope not in {"skills", "harness"}:
        raise ValueError("KernelBench edit_scope must be skills or harness")
    baseline = operator.get("baseline_harness") if example == "kernelbench" else None
    if scope == "skills" and not baseline:
        raise ValueError("Skill-only optimization requires operator baseline_harness")
    if baseline:
        absolute_path(baseline, "baseline_harness", directory=True)
    seed = absolute_path(
        args.seed or baseline or REPOSITORY / "examples" / example / "seed",
        "seed",
        directory=True,
    )
    if seed == prepared or prepared.is_relative_to(seed):
        raise ValueError("Private prepared data must be outside the candidate seed")
    ids = gpu_ids(operator, example)
    limits = ResearchLimits(
        max_agents=args.max_agents
        if args.max_agents is not None
        else (1 if example == "kernelbench" else 2),
        max_nodes=args.max_nodes,
        max_model_calls=args.max_model_calls,
        max_total_tokens=args.max_total_tokens,
        max_verify_calls=args.max_verify_calls,
        max_jobs=args.max_jobs,
        max_wall_seconds=args.max_wall_seconds,
        gpu_ids=ids,
        allowed_paths=("skills/*",) if scope == "skills" else ("*",),
    )
    if example == "cuhkx":
        absolute_path(prepared / "train/manifest.json", "training manifest")
        runner = operator.get("runner", {})
        if runner.get("kind", "docker") == "local" and not args.allow_local:
            raise ValueError("Local CUHK-X runner requires --allow-local and synthetic CPU data")
        if args.allow_local and (runner.get("kind") != "local" or ids):
            raise ValueError("--allow-local requires a local CPU runner")
    defaults = (
        {"search": "search", "final": "final", "diagnostic": "development"}
        if example == "kernelbench"
        else {"search": "validate", "final": "final", "diagnostic": "validate"}
    )
    phases = operator.get("phases", defaults)
    if not isinstance(phases, dict) or not {"search", "final"} <= phases.keys():
        raise ValueError("Operator phases must explicitly include search and independent final")
    expected_splits = {
        "search": "search" if example == "kernelbench" else "validate",
        "final": "final",
    }
    if "diagnostic" in phases:
        expected_splits["diagnostic"] = "development" if example == "kernelbench" else "validate"
    if any(phases[phase] != split for phase, split in expected_splits.items()):
        raise ValueError("Expected distinct search/final pools and a non-final diagnostic pool")
    pinned: dict[str, dict[str, Any]] = {}
    descriptions: dict[str, dict[str, Any]] = {}
    fields = ("protocol_id", "manifest_id", "split") + (
        ("target_model",) if example == "kernelbench" else ("prepared_protocol_id",)
    )
    for phase, split in expected_splits.items():
        value = describe(
            executable,
            wrapper,
            operator_path,
            example,
            phase,
            target_model,
            ids,
            args.preflight_timeout,
        )
        if (
            value.get("adapter") != example
            or value.get("phase") != phase
            or value.get("split") != split
        ):
            raise ValueError(f"{phase} describe returned a different adapter/phase/split")
        for field in fields:
            if not isinstance(value.get(field), str) or not value[field]:
                raise ValueError(f"{phase} describe has no valid {field}")
            if field.endswith("_id") and not re.fullmatch(r"[0-9a-f]{64}", value[field]):
                raise ValueError(f"{phase} describe has an invalid {field} digest")
        if example == "kernelbench" and value["target_model"] != target_model:
            raise ValueError("Describe target model differs from --target-model")
        if args.allow_local and value.get("dataset_kind") != "synthetic":
            raise ValueError("--allow-local is limited to explicitly synthetic data")
        pinned[phase] = {field: value[field] for field in fields}
        descriptions[phase] = value
    if pinned["search"]["manifest_id"] == pinned["final"]["manifest_id"]:
        raise ValueError("Search and final must use independent manifests")
    if operator_path.read_bytes() != operator_bytes:
        raise ValueError("Operator configuration changed during preflight; rerun with fixed inputs")
    base = (str(executable), str(wrapper))
    common = ("--config", str(operator_path), "--output", "{output}")
    if example == "kernelbench":
        command = (*base, "--harness", "{workspace}", *common, "--target-model", target_model)
        verifier_command = (*command, "--phase", "{phase}")
        commands = (
            (
                ApprovedCommand(
                    "develop",
                    (*command, "--phase", "diagnostic"),
                    ("workspace", "output"),
                    gpu_count=len(ids),
                    timeout_seconds=limits.max_wall_seconds,
                    output_subdir="public/development",
                ),
            )
            if "diagnostic" in pinned
            else ()
        )
        verify_timeout = limits.max_wall_seconds
        objective = "Improve a reusable KernelBench harness for " + target_model
        instructions = (
            f"Optimize {'only skills/*' if scope == 'skills' else 'the full harness'} for kernel "
            f"optimization on the fixed task model {target_model}. Keep changes general across "
            "tasks; never embed answers. Evaluate the initial harness on search as a baseline. "
            "Use the develop command and read_job_output_file to inspect development summaries, "
            "target code and trajectories; keep this material out of the candidate harness. "
            "Derive general improvements, assign focused research, and compare frozen candidates "
            "through the trusted verifier. Search/final raw tasks and traces are private. "
            "The target uses the existing DSH sandbox; no GPU development tool is supplied by "
            "the default seed. RSI decides when to perform the independent final confirmation."
        )
        metric = "fast_1"
    else:
        local = ("--allow-local",) if args.allow_local else ()
        train_timeout = (
            positive_seconds(
                operator.get("training", {}).get("timeout_seconds", 3600), "training timeout"
            )
            + 60
        )
        verify_timeout = (
            positive_seconds(
                operator.get("verification", {}).get("timeout_seconds", 600), "verification timeout"
            )
            + 60
        )
        commands = (
            ApprovedCommand(
                "train",
                (*base, "train", "--candidate", "{workspace}", *common, *local),
                ("workspace", "output"),
                gpu_count=len(ids),
                timeout_seconds=train_timeout,
                output_subdir="trained",
            ),
        )
        verifier_command = (
            *base,
            "verify",
            "--candidate",
            "{workspace}",
            *common,
            "--phase",
            "{phase}",
            *local,
        )
        objective = "Research and improve CUHK-X small-model training and design"
        instructions = (
            "Research papers and mainstream methods before choosing useful directions. Decide "
            "when to reproduce, branch, combine findings or stop within the configured limits. "
            "The seed is an unevaluated implementation reference, not a scored baseline. Optimize "
            "architecture, training and permitted multimodal design for 40-class activity "
            "recognition. No RGB camera input or large pretrained backbone; the entire inference "
            "candidate package must remain within 100,000,000 bytes. The user has confirmed "
            "organizer authorization for this LLM-assisted workflow. Train via the approved train "
            "command, wait without polling the model, then collect_job_output to destination "
            "trained so trained/checkpoint.pt is present before snapshotting and verification. "
            "Optional training.json requests bounded hyperparameters; operator resource/time "
            "limits remain fixed. Validation labels and original subject paths are private. "
            "Local held-out accuracy is not Kaggle leaderboard performance. RSI decides when "
            "to perform independent final verification; do not submit to Kaggle automatically."
        )
        if args.allow_local:
            instructions += " This is a real CPU execution on synthetic data, not competition data."
        metric = "accuracy"
    if args.instructions_file:
        instructions += "\n\nAdditional user guidance:\n" + args.instructions_file.read_text(
            encoding="utf-8"
        )
    return ResearchConfig(
        brief=TaskBrief(
            objective,
            instructions,
            limits,
            VerificationContract(
                metric,
                target=args.target,
                description="Frozen search comparison and independent final confirmation",
                identity={
                    "example": example,
                    "feedback": "aggregate_only",
                    "require_final_confirmation": True,
                },
            ),
        ),
        seed_dir=str(seed),
        base_url=args.base_url,
        commands=commands,
        verifier=ApprovedCommand(
            "verify",
            verifier_command,
            ("workspace", "output", "phase"),
            gpu_count=len(ids),
            timeout_seconds=verify_timeout,
        ),
        metadata={
            "template": example,
            "simulation_only": False,
            "evaluate_seed_as_baseline": example == "kernelbench",
            "verification_identity": {"adapter": example, "phases": pinned},
            "operator_config": str(operator_path),
            "operator_config_sha256": hashlib.sha256(operator_bytes).hexdigest(),
            "preflight": descriptions,
        },
    )


def write_config(config: ResearchConfig, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=output.parent, suffix=".json", delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(config.public(), stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
            load_config(temporary)  # Round-trip the exact serialized application schema.
            os.link(temporary, output)  # Atomic and refuses to replace an existing configuration.
        finally:
            temporary.unlink(missing_ok=True)


def main() -> None:
    cli = parser()
    args = cli.parse_args()
    try:
        config = build_config(args)
        write_config(config, args.output)
    except (OSError, ValueError, TypeError, KeyError) as error:
        cli.exit(2, f"Configuration failed: {error}\n")
    print(f"Research configuration written: {args.output}")
    print("Set RSI_API_KEY in the environment; no API key is stored in this file.")


if __name__ == "__main__":
    main()
