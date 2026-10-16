"""User/mount/PID namespace filesystem boundary, with no host data mount."""

from __future__ import annotations

import ctypes
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path


def mount(*args: str) -> None:
    subprocess.run(["mount", *args], check=True, capture_output=True)


def main() -> None:
    policy = json.loads(Path(sys.argv.pop(1)).read_text())
    root = Path(policy["rootfs"])
    root.mkdir(exist_ok=True)
    bindings = [(p, False) for p in policy["readable"]]
    bindings += [(p, True) for p in policy["writable"]]
    for name, writable in bindings:
        source = Path(name).resolve()
        if not source.exists():
            continue
        destination = root / str(source).lstrip("/")
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.touch()
        mount("--rbind", str(source), str(destination))
        if not writable:
            points = [
                (line.split()[4], line.split()[5])
                for line in Path("/proc/self/mountinfo").read_text().splitlines()
                if line.split()[4] == str(destination)
                or line.split()[4].startswith(str(destination) + "/")
            ]
            for point, flags in sorted(points, key=lambda item: len(item[0]), reverse=True):
                if "ro" in flags.split(","):
                    continue
                preserved = [f for f in flags.split(",") if f != "rw"]
                mount("-o", ",".join(["remount", "bind", "ro", *preserved]), point)
        # /bin and /lib aliases may resolve outside the original spelling.
        alias = root / name.lstrip("/")
        if alias != destination and not alias.exists():
            alias.parent.mkdir(parents=True, exist_ok=True)
            alias.symlink_to(str(source))
    for name in policy.get("readonly_resources", []):
        destination = root / name.lstrip("/")
        mount("--bind", str(destination), str(destination))
        mount("-o", "remount,bind,ro,nosuid", str(destination))
    (root / "proc").mkdir(exist_ok=True)
    os.chroot(root)
    os.chdir(policy["workspace"])
    # Tools cannot mount host paths or escape via privileged operations.
    libc = ctypes.CDLL(None, use_errno=True)

    class Header(ctypes.Structure):
        _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]

    class Caps(ctypes.Structure):
        _fields_ = [
            ("effective", ctypes.c_uint32),
            ("permitted", ctypes.c_uint32),
            ("inheritable", ctypes.c_uint32),
        ]

    header, caps = Header(0x20080522, 0), (Caps * 2)()
    if libc.capset(ctypes.byref(header), ctypes.byref(caps)) != 0:
        raise OSError(ctypes.get_errno(), "Cannot drop sandbox capabilities")
    if libc.prctl(38, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Cannot set no_new_privs")
    if policy.get("payload"):
        os.execv(policy["payload"][0], policy["payload"])
    sys.path.insert(0, policy["worker_code"])
    runpy.run_module("areal_rsi.modules.runtime_worker", run_name="__main__")


if __name__ == "__main__":
    main()
