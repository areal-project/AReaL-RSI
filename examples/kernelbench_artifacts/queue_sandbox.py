"""Small GPUQ job jail, called inside the queue's private mount/PID namespace.

Install as the queue's sandbox_exec.py. Keep scorer code and scheduling unchanged.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

JOB_ISOLATION = "filesystem-network-v1"


def mount(*args: str) -> None:
    subprocess.run(["/usr/bin/mount", *args], check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow", default="")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    adapter = Path(__file__).resolve().parent
    workspace = Path.cwd().resolve()
    job = workspace.parent
    if workspace.name != "workspace":
        raise ValueError("Expected a queue-owned job workspace")
    if not job.is_relative_to(adapter / "runtime"):
        raise ValueError("Job must belong to the queue runtime directory")
    task_id = json.loads((job / "job.json").read_text())["task_id"]
    cache_root = job / "cache"
    if task_id != "admin":
        cache_root = Path(os.environ["TMPDIR"]).resolve().parent
        expected = adapter / "runtime/cache/jobs" / task_id.replace("/", "_")
        if cache_root.parent != expected:
            raise ValueError("Compilation cache does not belong to this task")
    for variable, name in {
        "TMPDIR": "tmp",
        "TRITON_CACHE_DIR": "triton",
        "TORCH_EXTENSIONS_DIR": "torch",
        "TORCHINDUCTOR_CACHE_DIR": "inductor",
        "CUDA_CACHE_PATH": "cuda",
        "XDG_CACHE_HOME": "xdg",
    }.items():
        cache = cache_root / name
        cache.mkdir(parents=True, exist_ok=True)
        os.environ[variable] = str(cache)
    temporary = Path(os.environ["TMPDIR"])
    # The queue already creates a private mount/PID namespace. Network is local
    # to this job as well: no queue HTTP, SSH, or remote research data.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.unshare(0x40000000) != 0:  # CLONE_NEWNET
        raise OSError(ctypes.get_errno(), "Cannot isolate job network")
    jail = temporary / "rootfs"
    jail.mkdir(exist_ok=True)
    mount("-t", "tmpfs", "-o", "size=64m", "tmpfs", str(jail))

    def bind(
        source: Path,
        destination: str | None = None,
        *,
        writable: bool = False,
        recursive: bool = False,
    ) -> None:
        target = jail / (destination or str(source)).lstrip("/")
        source = source.resolve(strict=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            target.mkdir(exist_ok=True)
        elif not target.exists():
            target.touch()
        mount("--rbind" if recursive else "--bind", str(source), str(target))
        if not writable:
            mounts = {target}
            if recursive:
                mounts.update(
                    Path(line.split()[4])
                    for line in Path("/proc/self/mountinfo").read_text().splitlines()
                    if Path(line.split()[4]).is_relative_to(target)
                )
            for path in sorted(mounts, key=lambda p: len(p.parts), reverse=True):
                mount("-o", "remount,bind,ro", str(path))

    # Public runtime libraries; deliberately exclude /storage, /home, /root,
    # /run and the queue's state/credentials. Bind mounts do not copy dependencies.
    for name in ("/usr", "/opt/.venv", "/opt/conda", "/sys", "/.singularity.d/libs"):
        if Path(name).exists():
            bind(Path(name), recursive=True)
    for name in ("bin", "sbin", "lib", "lib64"):
        path = Path("/") / name
        if path.is_symlink():
            (jail / name).symlink_to(os.readlink(path))
        elif path.exists():
            bind(path)
    for name in (
        "ld.so.cache",
        "nsswitch.conf",
        "passwd",
        "group",
        "hosts",
        "resolv.conf",
        "localtime",
        "os-release",
        "alternatives",
    ):
        source = Path("/etc") / name
        if source.exists():
            bind(source, "/etc/" + name)
    # Only public scorer modules and references, never historical runs/results.
    for name in (
        "common.py",
        "kb_worker.py",
        "full_eval/strict_worker.py",
        "codex_eval/instrumented_worker.py",
    ):
        bind(adapter / name)
    benchmark = adapter.parent / "openrsi-kernelbench/KernelBench"
    # These small public directories are owner-only on the shared filesystem.
    # Copy them into the private read-only root instead of retaining DAC bypass.
    for name in ("src", "KernelBench"):
        target = jail / str(benchmark / name).lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(benchmark / name, target, ignore=shutil.ignore_patterns("__pycache__"))
    bind(job, writable=True)
    if not cache_root.is_relative_to(job):
        bind(cache_root, writable=True)  # Reuse this run's cache without exposing other runs.
    for name in ("/tmp", "/var/tmp"):
        bind(temporary, name, writable=True)
    (jail / "dev/shm").mkdir(parents=True)
    mount("-t", "tmpfs", "-o", "size=1g,mode=1777", "tmpfs", str(jail / "dev/shm"))
    # CUDA names its helper threads via /proc/self/task/*/comm. Keep the private
    # process view writable, while kernel-wide controls remain read-only.
    bind(Path("/proc"), writable=True)
    for name in ("/proc/sys", "/proc/sysrq-trigger", "/proc/irq", "/proc/bus"):
        if Path(name).exists():
            bind(Path(name))
    allowed = set(filter(None, args.allow.split(",")))
    devices = [Path("/dev") / name for name in ("null", "zero", "random", "urandom")]
    if allowed:
        devices += list(Path("/dev").glob("nvidia*"))
        devices += list(Path("/dev/nvidia-caps").glob("*"))
    for path in devices:
        gpu = re.fullmatch(r"nvidia(\d+)", path.name)
        if path.is_char_device() and (not gpu or gpu[1] in allowed):
            bind(path, writable=True)
    for name, target in {
        "fd": "/proc/self/fd",
        "stdin": "/proc/self/fd/0",
        "stdout": "/proc/self/fd/1",
        "stderr": "/proc/self/fd/2",
    }.items():
        (jail / "dev" / name).symlink_to(target)
    mount("-o", "remount,ro", str(jail))
    os.chroot(jail)
    os.chdir(workspace)
    os.environ["HOME"] = str(temporary)
    os.execvpe(
        "/usr/bin/setpriv",
        [
            "setpriv",
            "--bounding-set=-all",
            "--inh-caps=-all",
            "--ambient-caps=-all",
            "--no-new-privs",
            "--",
            *command,
        ],
        os.environ,
    )


if __name__ == "__main__":
    main()
