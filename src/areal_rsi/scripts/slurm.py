"""Controller-side prerequisites for AReaL's Slurm scheduler."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from areal_rsi.api.execution import SlurmPreflight
from areal_rsi.api.research import GenerationSpec


def _command(name: str) -> tuple[str, str]:
    found = shutil.which(name)
    if found is None:
        raise RuntimeError(f"Slurm command is unavailable: {name}")
    path = Path(found).resolve()
    result = subprocess.run(
        [str(path), "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    first_line = (result.stdout or result.stderr).splitlines()
    if not first_line or not first_line[0].strip():
        raise RuntimeError(f"Slurm command returned no version: {name}")
    return str(path), first_line[0].strip()[:200]


def _check_shared_directory(root: Path) -> None:
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"Slurm shared path is missing or symbolic: {root}")
    fd, temporary = tempfile.mkstemp(dir=root, prefix=".rsi-preflight-")
    path = Path(temporary)
    try:
        content = os.urandom(16)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if path.read_bytes() != content:
            raise OSError(f"Slurm shared path failed a read/write probe: {root}")
    finally:
        path.unlink(missing_ok=True)


def check_slurm(spec: GenerationSpec) -> SlurmPreflight:
    sbatch, sbatch_version = _command("sbatch")
    squeue, squeue_version = _command("squeue")
    scancel, scancel_version = _command("scancel")
    srun, srun_version = _command("srun")
    scontrol, scontrol_version = _command("scontrol")
    _check_shared_directory(Path(spec.cluster_fileroot))
    _check_shared_directory(Path(spec.name_resolve_root))
    return SlurmPreflight(
        sbatch,
        squeue,
        scancel,
        srun,
        scontrol,
        sbatch_version,
        squeue_version,
        scancel_version,
        srun_version,
        scontrol_version,
        spec.cluster_fileroot,
        spec.name_resolve_root,
    )
