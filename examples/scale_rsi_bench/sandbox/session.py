#!/usr/bin/env python3
"""Create and enter a history-free Scale RSI benchmark workspace."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
DEFAULT_RUNTIME_ROOT = REPO.parent / "scale_rsi_bench"
CODEX_NATIVE = Path(
    "/usr/local/lib/node_modules/@openai/codex/node_modules/@openai/"
    "codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex"
)
BENCHMARKS = {
    "nanogpt": ("nanogpt_env", "nano-gpt-data-curation"),
    "opsd": ("opsd_env", "on-policy-self-distillation"),
    "jailbreak": ("jailbreak_env", "jailbreak-robustness"),
    "swarm": ("swarm_env", "agent-swarm-optimization"),
}


def copy_item(source: Path, target: Path, *, ignore: shutil.IgnorePattern | None = None) -> None:
    if not source.exists():
        raise FileNotFoundError(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target, dirs_exist_ok=True, symlinks=False, ignore=ignore)
    else:
        shutil.copy2(source, target)


def create_session(benchmark: str, output: Path, runtime_root: Path) -> Path:
    env_name, task_name = BENCHMARKS[benchmark]
    runtime = (runtime_root / env_name).resolve()
    task = runtime / "source/tasks" / task_name
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"session directory is not empty: {output}")
    workspace = output / "workspace"
    for path in (workspace, output / "home/.codex", output / "tmp", output / "control"):
        path.mkdir(parents=True, exist_ok=True)

    copy_item(task / "instruction.md", workspace / "instruction.md")
    public_mounts: list[dict[str, str]] = []
    if benchmark == "nanogpt":
        copy_item(task / "environment/workspace", workspace)
        (workspace / "data").mkdir(exist_ok=True)
        for name, target in (
            ("pool/pool.jsonl", "data/pool.jsonl"),
            ("pool/pool_meta.npy", "data/pool_meta.npy"),
            ("workspace/data/multi_dev.npy", "data/multi_dev.npy"),
        ):
            source = runtime / name
            if source.exists():
                (workspace / target).touch()
                public_mounts.append({"source": str(source.resolve()), "target": target})
    elif benchmark == "opsd":
        copy_item(
            runtime / "workspace/repo",
            workspace / "repo",
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
        )
        for name in ("train_baseline.sh", "train_capped.sh", "dev_eval.sh"):
            copy_item(task / "environment/workspace" / name, workspace / name)
        copy_item(task / "environment/paper.pdf", workspace / "paper.pdf")
        (workspace / "submission").mkdir(exist_ok=True)
        (workspace / "submission/recipe.env").write_text(
            "# Optional whitelisted recipe variables. Empty means the published baseline.\n"
        )
    elif benchmark == "jailbreak":
        for name in ("train_baseline.sh", "dev_eval.sh", "README_data.md"):
            source = task / "environment/workspace" / name
            target = workspace / ("data/README.md" if name == "README_data.md" else name)
            copy_item(source, target)
        copy_item(runtime / "workspace/data", workspace / "data")
        copy_item(runtime / "workspace/eval", workspace / "eval")
        (workspace / "submission").mkdir(exist_ok=True)
    elif benchmark == "swarm":
        copy_item(task / "environment/workspace", workspace)
        (workspace / "submission").mkdir(exist_ok=True)

    metadata = {
        "schema_version": 1,
        "benchmark": benchmark,
        "created_at": int(time.time()),
        "workspace": str(workspace),
        "public_mounts": public_mounts,
    }
    (output / "session.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return output


def add_destination_parents(command: list[str], destinations: list[Path]) -> None:
    parents: set[Path] = set()
    for destination in destinations:
        current = destination.parent
        while current != current.parent:
            parents.add(current)
            current = current.parent
    for parent in sorted(parents, key=lambda value: (len(value.parts), str(value))):
        command.extend(["--dir", str(parent)])


def sandbox_command(session: Path, inner: list[str]) -> list[str]:
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise RuntimeError("Bubblewrap is required; install bwrap or put it in PATH")
    metadata = json.loads((session / "session.json").read_text())
    home, workspace, temp = session / "home", session / "workspace", session / "tmp"
    destinations = [Path("/home/codex"), Path("/workspace"), Path("/tmp")]
    destinations.extend(Path("/workspace") / item["target"] for item in metadata["public_mounts"])
    command = [
        bwrap,
        "--unshare-user",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-cgroup",
        "--die-with-parent",
        "--new-session",
        "--dev",
        "/dev",
        "--dir",
        "/proc",
    ]
    command.extend(["--dir", "/proc/self", "--ro-bind", "/proc/self", "/proc/self"])
    command.extend(["--dir", "/run", "--tmpfs", "/run"])
    add_destination_parents(command, destinations)
    for path in ("/usr", "/usr/local", "/opt/conda", "/opt/node-v22.23.2-linux-x64"):
        if Path(path).exists():
            command.extend(["--ro-bind", path, path])
    for link in ("/bin", "/sbin", "/lib", "/lib64"):
        path = Path(link)
        if path.is_symlink():
            command.extend(["--symlink", os.readlink(path), link])
    for path in (
        "/etc/ssl",
        "/etc/pki",
        "/etc/resolv.conf",
        "/etc/hosts",
        "/etc/nsswitch.conf",
        "/etc/passwd",
        "/etc/group",
        "/etc/localtime",
    ):
        if Path(path).exists():
            command.extend(["--ro-bind", path, path])
    command.extend(
        [
            "--bind",
            str(home),
            "/home/codex",
            "--bind",
            str(workspace),
            "/workspace",
            "--bind",
            str(temp),
            "/tmp",
        ]
    )
    for item in metadata["public_mounts"]:
        command.extend(
            ["--ro-bind", item["source"], str(Path("/workspace") / item["target"])]
        )
    # Slurm clients would create an unsandboxed process on a compute node. A scoped
    # submission bridge must be used instead of exposing these general-purpose CLIs.
    for name in ("sbatch", "srun", "salloc", "scancel"):
        path = Path("/usr/bin") / name
        if path.exists():
            command.extend(["--ro-bind", "/dev/null", str(path)])
    command.extend(
        [
            "--setenv",
            "HOME",
            "/home/codex",
            "--setenv",
            "CODEX_HOME",
            "/home/codex/.codex",
            "--setenv",
            "TMPDIR",
            "/tmp",
            "--setenv",
            "PATH",
            "/usr/local/bin:/opt/node-v22.23.2-linux-x64/bin:/opt/conda/bin:/usr/bin:/bin",
            "--setenv",
            "SHELL",
            "/bin/bash",
            "--chdir",
            "/workspace",
            "--",
            *inner,
        ]
    )
    return command


def codex_config(session: Path, model: str | None, base_url: str | None) -> None:
    values = [
        f'model = {json.dumps(model or os.environ.get("RSI_MODEL", "gpt-5.6-sol"))}',
        'model_provider = "rsi"',
        'approval_policy = "never"',
        'sandbox_mode = "danger-full-access"',
        "",
        "[model_providers.rsi]",
        'name = "RSI benchmark gateway"',
        f'base_url = {json.dumps(base_url or os.environ.get("RSI_BASE_URL", "https://api.openai.com/v1"))}',
        'env_key = "RSI_API_KEY"',
        'wire_api = "responses"',
        "",
    ]
    (session / "home/.codex/config.toml").write_text("\n".join(values))


def clean_environment() -> dict[str, str]:
    allowed = {
        "TERM",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "RSI_API_KEY",
        "OPENAI_API_KEY",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    }
    env = {name: value for name, value in os.environ.items() if name in allowed}
    env.setdefault("TERM", "xterm-256color")
    env["NODE_USE_ENV_PROXY"] = "1"
    return env


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("benchmark", choices=BENCHMARKS)
    create.add_argument("output", type=Path)
    run = sub.add_parser("run")
    run.add_argument("session", type=Path)
    run.add_argument("argv", nargs=argparse.REMAINDER)
    codex = sub.add_parser("codex")
    codex.add_argument("session", type=Path)
    codex.add_argument("--model")
    codex.add_argument("--base-url")
    codex.add_argument("prompt", nargs="?")
    args = parser.parse_args()
    if args.command == "create":
        print(create_session(args.benchmark, args.output.resolve(), args.runtime_root.resolve()))
        return
    session = args.session.resolve()
    if args.command == "run":
        if not args.argv:
            parser.error("run requires a command after the session path")
        command = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
    else:
        codex_config(session, args.model, args.base_url)
        command = [
            str(CODEX_NATIVE),
            "--dangerously-bypass-approvals-and-sandbox",
            "--no-alt-screen",
            "-C",
            "/workspace",
        ]
        if args.prompt:
            command.append(args.prompt)
    completed = subprocess.run(sandbox_command(session, command), env=clean_environment())
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
