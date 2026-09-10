"""Complete 3D workspaces with host-owned validation and reproducible dependencies."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4

from areal_rsi.base.storage import write_json
from areal_rsi.modules.runtime import sandbox_wrapper

ENVIRONMENT_VERSION = "contract-v2-project-and-validation-v2"
PUBLIC_FILES = (
    "package.json",
    "package-lock.json",
    "index.html",
    "CONTRACT_V2.md",
    "src/main.js",
    "src/style.css",
    "src/model.js",
)
MAX_SOURCE_BYTES = 2_000_000
SNAPSHOT_COMMIT = "d7a9504cc6bb5b1918a084ef62225688a67c3aa6"
SNAPSHOT_DIGEST = "b799e9f0f3e6c850fa4cb7552f2f659054866893a828d867dd007f376b2c416f"


class ModelEnvironment:
    """Share a prepared dependency snapshot, never an agent-modifiable evaluator."""

    def __init__(self, template: Path, prepared: Path):
        self.template = template.resolve()
        self.prepared = prepared.resolve()

    def check(self) -> None:
        """Fail before paid calls if the fixed viewer or its dependencies are incomplete."""
        if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").is_file():
            raise RuntimeError("3D validation requires macOS sandbox-exec")
        if shutil.which("node") is None:
            raise RuntimeError("Node.js is unavailable")
        checker = self.template / "scripts/check-model.mjs"
        if not checker.is_file():
            raise RuntimeError(f"Missing Contract V2 checker: {checker}")
        manifest = json.loads((self.template.parent / "snapshot.json").read_text())
        # Match runtime.mjs's compact JSON digest, including the recorded file order.
        manifest_digest = hashlib.sha256(
            json.dumps(manifest["files"], separators=(",", ":")).encode()
        ).hexdigest()
        if manifest.get("commit") != SNAPSHOT_COMMIT or manifest_digest != SNAPSHOT_DIGEST:
            raise ValueError("Runtime manifest does not match the pinned snapshot")
        if "scripts/check-model.mjs" not in manifest["files"]:
            raise ValueError("Runtime snapshot must pin the Contract V2 checker")
        for name, digest in manifest["files"].items():
            if hashlib.sha256((self.template / name).read_bytes()).hexdigest() != digest:
                raise ValueError(f"Pinned template changed: {name}")
        for name in PUBLIC_FILES:
            if (self.prepared / name).read_bytes() != (self.template / name).read_bytes():
                raise ValueError(f"Prepared runtime differs from template: {name}")
        package = json.loads((self.prepared / "package.json").read_text())
        if package.get("type") != "module":
            raise ValueError("The viewer must use explicit ESM")
        for name in ("three/package.json", "vite/bin/vite.js", ".bin/vite"):
            if not (self.prepared / "node_modules" / name).is_file():
                raise ValueError(f"Missing dependency: {name}; run the prepare command first")

    def prepare_workspace(self, workspace: Path) -> None:
        """Expose the runnable viewer, but not validator implementation or fixtures."""
        self.check()
        workspace.mkdir(parents=True, exist_ok=False)
        for name in PUBLIC_FILES:
            destination = workspace / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.template / name, destination)
        # Copies, not shared writable links: one task cannot poison another task's packages.
        shutil.copytree(self.prepared / "node_modules", workspace / "node_modules", symlinks=True)

    def validate(self, source: bytes, folder: Path) -> dict[str, Any]:
        """Run contract and build independently in a fresh sandbox; retain both logs."""
        if len(source) > MAX_SOURCE_BYTES:
            raise ValueError("Model source exceeds the fixed 2 MB limit")
        folder.mkdir(parents=True, exist_ok=False)
        self.check()
        digest = hashlib.sha256(source).hexdigest()
        (folder / "model.js").write_bytes(source)
        # Temporary dependencies are disposable; source, checks and receipts remain on disk.
        with tempfile.TemporaryDirectory(prefix="evaluation-", dir=folder) as temporary:
            workspace = Path(temporary) / "viewer"
            shutil.copytree(self.template, workspace)
            shutil.copytree(
                self.prepared / "node_modules", workspace / "node_modules", symlinks=True
            )
            (workspace / "src/model.js").write_bytes(source)
            sandbox = Path(temporary) / "sandbox"
            sandbox.mkdir()
            node_name = shutil.which("node")
            if node_name is None:
                raise RuntimeError("Node.js is unavailable")
            node = Path(node_name).resolve()
            sandbox_wrapper(sandbox, workspace, node)
            profile = sandbox / "sandbox.sb"
            grants = []
            # Node checks inherited regular-file descriptors during startup. Grant only
            # these four output files, not their parent or any historical evidence.
            for name in ("contract.stdout", "contract.stderr", "build.stdout", "build.stderr"):
                grants.append(
                    f"(allow file-read* file-write* (literal {json.dumps(str(folder / name))}))"
                )
            profile.write_text(profile.read_text() + "\n(deny network*)\n" + "\n".join(grants))
            checks = {}
            for name, args in (
                ("contract", [str(node), "scripts/check-model.mjs"]),
                ("build", [str(node), "node_modules/vite/bin/vite.js", "build"]),
            ):
                checks[name] = self._run_check(name, args, workspace, profile, folder)
        result = {
            "environment": ENVIRONMENT_VERSION,
            "source_sha256": digest,
            "passed": all(check["returncode"] == 0 for check in checks.values()),
            "checks": checks,
        }
        # A zero exit without the checker's receipt is not a valid contract result.
        if checks["contract"]["returncode"] == 0:
            try:
                receipt = json.loads(checks["contract"]["stdout"])
                valid = receipt.get("ok") is True and receipt.get("sourceSha256") == digest
            except (ValueError, AttributeError):
                valid = False
            if not valid:
                result["passed"] = False
                result["receipt_error"] = "Missing or mismatched contract receipt"
        write_json(folder / "result.json", result, immutable=True)
        return result

    @staticmethod
    def _run_check(
        name: str, args: list[str], workspace: Path, profile: Path, folder: Path
    ) -> dict[str, Any]:
        """Bound candidate execution time/output, and terminate its entire process group."""
        stdout_path, stderr_path = folder / f"{name}.stdout", folder / f"{name}.stderr"
        command = [
            "/usr/bin/sandbox-exec",
            "-f",
            str(profile),
            "/bin/bash",
            "-c",
            'ulimit -f 4096; exec "$@"',
            "check",
            *args,
        ]
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(
                command,
                cwd=workspace,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "GAMEAGENT_GDD_STYLE_ID": "piggy-island",
                    "HOME": str(workspace),
                    "TMPDIR": str(workspace),
                },
            )
            timed_out = False
            try:
                process.wait(timeout=120)
            except subprocess.TimeoutExpired:
                timed_out = True
            finally:
                # Also remove descendants left behind by a candidate that already exited.
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        return {
            "returncode": process.returncode,
            "timed_out": timed_out,
            "stdout": stdout_path.read_text(errors="replace"),
            "stderr": stderr_path.read_text(errors="replace"),
        }


class ValidationService:
    """Per-task authenticated check endpoint; accepts source bytes, never host paths."""

    def __init__(self, environment: ModelEnvironment, root: Path, *, max_checks: int = 25):
        self.environment, self.root = environment, root
        self.max_checks = max_checks
        self.token = uuid4().hex
        self.lock = threading.Lock()
        self.count = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(10)

            def log_message(self, *_args: Any) -> None:
                pass

            def do_POST(self) -> None:
                if self.path != "/validate" or not hmac.compare_digest(
                    self.headers.get("Authorization", ""), "Bearer " + owner.token
                ):
                    self.send_error(403)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= MAX_SOURCE_BYTES * 2:
                        raise ValueError("Invalid source payload length")
                    payload = json.loads(self.rfile.read(size))
                    source = base64.b64decode(payload["source"], validate=True)
                    if len(source) > MAX_SOURCE_BYTES:
                        raise ValueError("Source too large")
                except (ValueError, TypeError, KeyError):
                    self.send_error(400)
                    return
                if not owner.lock.acquire(blocking=False):
                    self.send_error(429, "A check is already running")
                    return
                try:
                    if owner.count >= owner.max_checks:
                        self.send_error(429, "Task validation budget exhausted")
                        return
                    owner.count += 1
                    result = owner.environment.validate(source, owner.root / str(owner.count))
                    body = json.dumps(result).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except Exception as error:
                    write_json(
                        owner.root / f"error-{owner.count}.json",
                        {
                            "error_type": type(error).__name__,
                            "message": str(error),
                        },
                    )
                    self.send_error(500, "Validation infrastructure failed; host logs retained")
                finally:
                    owner.lock.release()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def install_client(self, workspace: Path) -> None:
        """Install callable wrappers and capability documentation, not checker source."""
        scripts = workspace / "scripts"
        scripts.mkdir()
        endpoint = f"http://127.0.0.1:{self.server.server_port}/validate"
        client = (
            "import {readFile,writeFile,mkdir} from 'node:fs/promises';\n"
            "import {dirname,resolve} from 'node:path';\n"
            f"const endpoint={json.dumps(endpoint)}, token={json.dumps(self.token)};\n"
            "const source=await readFile(new URL('../src/model.js',import.meta.url));\n"
            "const response=await fetch(endpoint,{method:'POST',headers:{"
            "Authorization:'Bearer '+token,'Content-Type':'application/json'},"
            "body:JSON.stringify({source:source.toString('base64')})});\n"
            "if(!response.ok) throw new Error('Host validation unavailable: '+response.status);\n"
            "const result=await response.json();\n"
            "const output=resolve(process.argv[2]||'.validation/model-validation.json');\n"
            "await mkdir(dirname(output),{recursive:true});\n"
            "await writeFile(output,JSON.stringify(result,null,2)+'\\n');\n"
            "for(const [name,check] of Object.entries(result.checks)){\n"
            " console.log(name+': '+(check.returncode===0?'passed':'failed'));\n"
            " if(check.returncode!==0) console.error(check.stderr);\n"
            "}\n"
            "if(result.receipt_error) console.error(result.receipt_error);\n"
            "console.log('Full validation result: '+output);\n"
            "process.exitCode=result.passed?0:1;\n"
        )
        (scripts / "check-model.mjs").write_text(client)
        (scripts / "validate-model.sh").write_text(
            '#!/usr/bin/env bash\nset -euo pipefail\nnode scripts/check-model.mjs "$@"\n'
        )
        (workspace / "CAPABILITIES.md").write_text(
            "# Workspace capabilities\n\n"
            "Three.js and Vite dependencies are installed. Only src/model.js is a deliverable.\n"
            "Use CONTRACT_V2.md for the public contract; keep the supplied helpers intact.\n\n"
            "- Syntax: `node --check src/model.js`\n"
            "- Build: `npm run build`\n"
            "- Contract and build: `npm run validate:model -- .validation/model-validation.json`\n"
            "- Local preview: `npm run dev -- --host 127.0.0.1`\n\n"
            "The validation command sends model.js to a host-owned isolated checker.\n"
            "Its implementation and fixtures are not available to read or edit.\n"
            "Use relative report paths; the original container's /output path is not mounted.\n"
            "Do not change the wrappers, dependencies, project files or TARGET.md.\n"
            "Final evaluation uses fresh fixed files; editing a local receipt cannot pass it.\n"
            f"At most {self.max_checks} validation calls are available per generation.\n"
        )

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
