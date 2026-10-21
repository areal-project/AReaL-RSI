"""Prepare pinned shared assets and four native, single-GPU nanoGPT workspaces."""

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from env_paths import runtime_root
from local_job import resolve_gpu

PIN = "3061df5c8fa466830fca8c82fe2b7a1f14708b62"
CODE = Path(__file__).resolve().parents[1]


def link(source, destination):
    if destination.is_symlink() and destination.resolve() == source.resolve():
        return
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"refusing to replace existing path: {destination}")
    destination.symlink_to(source.resolve(), target_is_directory=source.is_dir())


def layout(root):
    root.mkdir(parents=True, exist_ok=True)
    for name in ("scripts", "bench.sh", "requirements-nanogpt.txt"):
        link(CODE / name, root / name)
    for name in (
        "logs",
        "pool",
        "workspace/data",
        "workspace/submission",
        "cache/huggingface",
        "cache/uv",
        "cache/pip",
    ):
        (root / name).mkdir(parents=True, exist_ok=True)


def stage(root):
    task = root / "source/tasks/nano-gpt-data-curation"
    for source in (task / "environment/workspace").iterdir():
        target = root / "workspace" / source.name
        if not target.exists():
            shutil.copy2(source, target)
    for source, target in (
        (task / "instruction.md", root / "workspace/instruction.md"),
        (task / "environment/assets/multi_dev.npy", root / "workspace/data/multi_dev.npy"),
    ):
        if not target.exists():
            shutil.copy2(source, target)


def create_workers(root, gpu_ids):
    uuids = [resolve_gpu(value) for value in gpu_ids]
    if len(set(uuids)) != len(uuids):
        raise ValueError("each worker must use a distinct physical GPU")
    workers = []
    for index, (gpu, uuid) in enumerate(zip(gpu_ids, uuids, strict=True)):
        worker = root / "workers" / f"worker-{index}"
        worker.mkdir(parents=True, exist_ok=True)
        assignment = {"worker": index, "gpu_index": gpu, "gpu_uuid": uuid, "shared_root": str(root)}
        assignment_path = worker / "assignment.json"
        if assignment_path.exists() and json.loads(assignment_path.read_text()) != assignment:
            raise ValueError(f"worker assignment changed: {worker}")
        for name in ("source", ".venv", "pool", "scripts", "requirements-nanogpt.txt"):
            link(root / name, worker / name)
        for name in (
            "workspace/data",
            "workspace/submission",
            "experiments",
            "logs",
            "cache/triton",
            "cache/torch",
            "tmp",
        ):
            (worker / name).mkdir(parents=True, exist_ok=True)
        link(root / "cache/huggingface", worker / "cache/huggingface")
        for name in ("pool.jsonl", "pool_meta.npy"):
            link(root / "pool" / name, worker / "workspace/data" / name)
        link(root / "workspace/data/multi_dev.npy", worker / "workspace/data/multi_dev.npy")
        for source in (
            root / "source/tasks/nano-gpt-data-curation/environment/workspace"
        ).iterdir():
            target = worker / "workspace" / source.name
            if not target.exists():
                shutil.copy2(source, target)
        instruction = worker / "workspace/instruction.md"
        if not instruction.exists():
            upstream = (root / "source/tasks/nano-gpt-data-curation/instruction.md").read_text()
            instruction.write_text(
                f"Local workspace: {worker / 'workspace'}\n"
                f"Assigned GPU: physical {gpu}, {uuid}. Launch with bash ../run.sh.\n"
                "In the upstream instructions below, /workspace means this directory.\n"
                "Only curate data; the evaluator always runs the pinned trainer.\n"
                "Use bash ../run.sh submission/selection.json for a full DEV run; "
                "add --smoke for validation.\n"
                "Save the printed run_dir; query with bash ../run.sh status RUN_DIR.\n"
                "The upstream hidden-test anchor is not a DEV result.\n\n" + upstream
            )
        exports = {
            "RSI_ROOT": str(worker),
            "RSI_SHARED_ROOT": str(root),
            "RSI_NANOGPT_BACKEND": "local",
            "CUDA_VISIBLE_DEVICES": uuid,
            "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
            "HF_HOME": str(root / "cache/huggingface"),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "PYTHONNOUSERSITE": "1",
            "TOKENIZERS_PARALLELISM": "false",
            "OMP_NUM_THREADS": "16",
            "MKL_NUM_THREADS": "16",
            "TRITON_CACHE_DIR": str(worker / "cache/triton"),
            "TORCHINDUCTOR_CACHE_DIR": str(worker / "cache/torch"),
            "TMPDIR": str(worker / "tmp"),
        }
        env_text = "#!/usr/bin/env bash\n" + "".join(
            f"export {name}={shlex.quote(value)}\n" for name, value in exports.items()
        )
        env_text += f'export PATH={shlex.quote(str(root / ".venv/bin"))}:"$PATH"\n'
        (worker / "env.sh").write_text(env_text)
        (worker / "run.sh").write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            'source "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")/env.sh"\n'
            f'exec bash {shlex.quote(str(CODE / "bench.sh"))} "$@"\n'
        )
        (worker / "run.sh").chmod(0o755)
        assignment_path.write_text(json.dumps(assignment, indent=2) + "\n")
        # Replace only the deployment wrapper; the upstream trainer/model stay unchanged.
        (worker / "workspace/dev_eval.sh").write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            'here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)\n'
            'exec bash "$here/../run.sh" "${1:-$here/submission/selection.json}"\n'
        )
        workers.append({**assignment, "root": str(worker), "workspace": str(worker / "workspace")})
    (root / "workers.json").write_text(json.dumps(workers, indent=2) + "\n")
    return workers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=runtime_root(__file__))
    parser.add_argument("--gpus", default="0,1,2,3")
    parser.add_argument(
        "--assets-ready",
        action="store_true",
        help="validate/stage already prepared assets; skip install/download/build",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    layout(root)
    env = {
        **os.environ,
        "RSI_ROOT": str(root),
        "HF_HOME": str(root / "cache/huggingface"),
        "UV_CACHE_DIR": str(root / "cache/uv"),
        "HF_HUB_OFFLINE": "0",
        "TRANSFORMERS_OFFLINE": "0",
    }
    source = root / "source"
    if not source.exists():
        subprocess.run(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--sparse",
                "https://github.com/scaleapi/rsi-benchmark.git",
                str(source),
            ],
            check=True,
        )
        subprocess.run(["git", "-C", str(source), "checkout", PIN], check=True)
        subprocess.run(
            ["git", "-C", str(source), "sparse-checkout", "set", "tasks/nano-gpt-data-curation"],
            check=True,
        )
    actual = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    if actual != PIN:
        raise ValueError(f"unexpected upstream source revision {actual}")
    python = root / ".venv/bin/python"
    if not args.assets_ready:
        if not python.is_file():
            subprocess.run(
                ["uv", "venv", "--python", sys.executable, str(root / ".venv")], check=True
            )
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(python),
                "--index-url",
                "https://pypi.org/simple",
                "-r",
                str(CODE / "requirements-nanogpt.txt"),
            ],
            check=True,
            env=env,
        )
        subprocess.run([str(python), str(CODE / "scripts/download_data.py")], check=True, env=env)
    stage(root)
    subprocess.run(["uv", "pip", "check", "--python", str(python)], check=True)
    with (root / "logs/installed-requirements.txt").open("w") as installed:
        subprocess.run(
            ["uv", "pip", "freeze", "--python", str(python)], check=True, stdout=installed
        )
    subprocess.run([str(python), str(CODE / "scripts/build_data.py")], check=True, env=env)
    # Shared benchmark inputs are immutable; each worker writes only its own outputs.
    task = root / "source/tasks/nano-gpt-data-curation"
    protected = [
        *task.rglob("*"),
        *(root / "pool").iterdir(),
        root / "workspace/data/multi_dev.npy",
    ]
    for asset in protected:
        if asset.is_file():
            asset.chmod(asset.stat().st_mode & ~0o222)
    workers = create_workers(root, args.gpus.split(","))
    print(
        json.dumps({"shared_root": str(root), "upstream_commit": PIN, "workers": workers}, indent=2)
    )


if __name__ == "__main__":
    main()
