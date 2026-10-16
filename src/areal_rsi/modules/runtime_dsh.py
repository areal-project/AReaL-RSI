"""Prepare the matching, packaged DSH Runtime bundle outside the sandbox."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path


def ensure_profile(root: Path, *, binary: Path | None = None) -> Path:
    archive = Path(__file__).parents[1] / "resources/runtime/areal-agent-runtime-dsh-0.3.0.tgz"
    digest = hashlib.sha256(
        archive.read_bytes() + (archive.parent / "pnpm-lock.yaml").read_bytes()
    ).hexdigest()
    home = root.resolve() / "runtime-dsh" / digest[:16]
    home.mkdir(parents=True, exist_ok=True)
    with (home / ".install.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        profile = home / "profiles/agent-runtime"
        marker = home / ".ready"
        if marker.exists() and marker.read_text() == digest:
            if binary is not None and not (home / "profiles/node_modules").exists():
                _prepare_module_fallback(home, binary)
            return home
        if not shutil.which("pnpm"):
            raise RuntimeError("DSH Runtime profile requires pnpm and Node.js 22.19+ / 24+")
        profile.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(archive, profile / "runtime.tgz")
        (profile / "package.json").write_text(
            json.dumps(
                {
                    "name": "rsi-agent-runtime-profile",
                    "private": True,
                    "dsh": {
                        "profile": {
                            "bundles": [
                                "@deepseek-ai/dsh-base",
                                "@deepseek-ai/dsh-sdk-app",
                                "@areal/agent-runtime-dsh",
                            ],
                            "patchReload": "startup",
                        }
                    },
                    "dependencies": {"@areal/agent-runtime-dsh": "file:runtime.tgz"},
                }
            )
        )
        (profile / "cordis.yml").write_text("[]\n")
        (profile / "cordis.patch.yml").write_text("[]\n")
        shutil.copyfile(archive.parent / "pnpm-lock.yaml", profile / "pnpm-lock.yaml")
        with (home / "install.log").open("wb") as log:
            subprocess.run(
                ["pnpm", "install", "--frozen-lockfile", "--ignore-scripts"],
                cwd=profile,
                check=True,
                timeout=180,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        if binary is not None:
            _prepare_module_fallback(home, binary)
        marker.write_text(digest)
        return home


def _prepare_module_fallback(home: Path, binary: Path) -> None:
    """Prime DSH's managed ESM proxies once, before readonly session mounts.

    DSH composes these during config dumping; this performs no model inference.
    Copying only the profile's pnpm node_modules forces every new session to
    regenerate the shared profiles/node_modules fallback and can exceed SDK startup timeouts.
    """
    with (home / "module-fallback.log").open("wb") as log:
        subprocess.run(
            [str(binary), "--profile", "agent-runtime", "--dump-config"],
            env={**os.environ, "DSH_HOME": str(home), "DSH_TELEMETRY_DISABLED": "1"},
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=180,
        )
    if not (home / "profiles/node_modules").is_dir():
        raise RuntimeError("DSH module fallback was not materialized")
