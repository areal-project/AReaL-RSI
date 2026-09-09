"""Prepare the matching, packaged DSH Runtime bundle outside the sandbox."""

from __future__ import annotations

import fcntl
import hashlib
import json
import shutil
import subprocess
from pathlib import Path


def ensure_profile(root: Path) -> Path:
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
        marker.write_text(digest)
        return home
