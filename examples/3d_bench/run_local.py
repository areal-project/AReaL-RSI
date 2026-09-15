"""One real RSI round over a selected 3D task suite, with explicit sample reuse."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
from pathlib import Path

if __package__:
    from .environment import ENVIRONMENT_VERSION, PUBLIC_FILES, ModelEnvironment, ValidationService
else:
    from environment import ENVIRONMENT_VERSION, PUBLIC_FILES, ModelEnvironment, ValidationService

from areal_rsi.api.base import AgentSpec, TaskResult
from areal_rsi.base.storage import decode, encode, new_id, write_json

TASK_MESSAGE = "Read TARGET.md and CAPABILITIES.md, then implement src/model.js."
GENERATION_ENVIRONMENT = ENVIRONMENT_VERSION
EXAMPLE = Path(__file__).resolve().parent
DATASET = EXAMPLE.parents[1] / ".rsi/datasets/3d-model-v2"
CASE = EXAMPLE / "cases"
BASELINE_PROMPT = """Complete TARGET.md using the supplied Three.js framework.
Only edit the body of createModel in src/model.js and preserve the framework helpers.
Use the editor tool to write src/model.js directly; do not embed the full source in
python -c, shell heredocs, or workspace.run arguments. Use bash only to run the
available build and validation commands. Use CONTRACT_V2.md as the authoritative
interface and deliver src/model.js as a file.
"""


def prepare_public_workspace(template: Path, workspace: Path) -> None:
    """Copy only the public model inputs into a fresh Task Agent workspace."""
    package = json.loads((template / "package.json").read_text())
    if package.get("type") != "module":
        raise ValueError("Contract V2 template must explicitly declare type=module")
    for name in ("src/model.js", "CONTRACT_V2.md", "package.json"):
        destination = workspace / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(template / name, destination)


class FileTaskRuntime:
    def __init__(
        self,
        root,
        content,
        runtime,
        template,
        emit,
        *,
        retry_incomplete=False,
        environment=None,
        reuse_outputs=False,
    ):
        self.root, self.content, self.runtime = root, content, runtime
        self.template, self.emit = template, emit
        self.retry_incomplete = retry_incomplete
        self.environment = environment
        self.reuse_outputs = reuse_outputs
        self.reuse_locks = {}

    async def run(self, solution, task, *, config=None):
        if not self.reuse_outputs:
            return await self._generate(solution, task, config=config)
        identity = {"solution": encode(solution), "task": encode(task)}
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        # TRAIN deliberately uses the recorded baseline samples in same-suite experiments.
        # A per-sample lock also prevents duplicate paid calls from concurrent consumers.
        async with self.reuse_locks.setdefault(key, asyncio.Lock()):
            cache = self.root / "shared-samples" / f"{key}.json"
            if cache.exists():
                saved = json.loads(cache.read_text())
                if saved["identity"] != identity:
                    raise ValueError("Shared sample identity mismatch")
                execution_key = config["execution_key"]
                write_json(
                    self.root
                    / "reused"
                    / (hashlib.sha256(execution_key.encode()).hexdigest() + ".json"),
                    {
                        "execution_key": execution_key,
                        "origin": saved["execution_key"],
                        "result": saved["result"],
                    },
                    immutable=True,
                )
                self.emit(f"Reuse recorded Task sample: {execution_key}")
                return decode(TaskResult, saved["result"])
            result = await self._generate(solution, task, config=config)
            write_json(
                cache,
                {"identity": identity, "execution_key": config["execution_key"], "result": result},
                immutable=True,
            )
            return result

    async def _generate(self, solution, task, *, config=None):
        execution_key = (config or {})["execution_key"]
        folder = self.root / hashlib.sha256(execution_key.encode()).hexdigest()[:16]
        folder.mkdir(parents=True, exist_ok=True)
        identity = {"execution_key": execution_key, "solution": solution, "task": task}
        write_json(folder / "inputs.json", identity, immutable=True)
        if (folder / "task-result.json").exists():
            return decode(TaskResult, json.loads((folder / "task-result.json").read_text()))
        if (folder / "workspace").exists():
            if not self.retry_incomplete:
                raise ValueError("Incomplete generation exists; refusing an implicit paid retry")
            archived = self.root / "failed-attempts" / new_id(folder.name)
            archived.parent.mkdir(parents=True, exist_ok=True)
            folder.rename(archived)
            folder.mkdir()
            write_json(folder / "inputs.json", identity, immutable=True)
            write_json(
                folder / "retry.json",
                {
                    "previous_attempt": str(archived),
                    "execution_key": execution_key,
                    "reason": "Explicit incomplete-generation retry; earlier evidence retained",
                },
            )
            self.emit(f"Retry incomplete Task Agent; previous files retained at {archived}")
        workspace = folder / "workspace"
        service = None
        if self.environment is None:
            # Kept for small offline fixtures; real runs always use the complete environment.
            prepare_public_workspace(self.template, workspace)
        else:
            await asyncio.to_thread(self.environment.prepare_workspace, workspace)
        (workspace / "TARGET.md").write_text(str(task.input))
        await self.content.materialize(solution.harness.content, folder / "harness")
        prompt = await self.content.put_text((folder / "harness/prompt.md").read_text())
        self.emit(f"Task Agent start: {execution_key}")
        # Start the service only after fallible preparation; always close it below.
        try:
            if self.environment is not None:
                service = ValidationService(self.environment, folder / "self-checks")
                service.install_client(workspace)
            return await self._execute(solution, task, folder, workspace, prompt, service)
        finally:
            if service is not None:
                await asyncio.to_thread(service.close)

    async def _execute(self, solution, task, folder, workspace, prompt, service):
        """Run one generation while the caller owns the validation service lifetime."""
        fixed = (
            ("package.json", "CONTRACT_V2.md", "TARGET.md")
            if service is None
            else (
                *(name for name in PUBLIC_FILES if name != "src/model.js"),
                "TARGET.md",
                "CAPABILITIES.md",
                "scripts/check-model.mjs",
                "scripts/validate-model.sh",
            )
        )
        hashes = {
            name: hashlib.sha256((workspace / name).read_bytes()).hexdigest() for name in fixed
        }
        write_json(folder / "public-inputs.json", hashes, immutable=True)
        session = None
        try:
            session = await self.runtime.open(
                AgentSpec(
                    "task-agent",
                    "deepseek-harness@v1",
                    solution.model,
                    (prompt,),
                    tools=("bash@v1", "editor@v1"),
                ),
                workspace_root=str(workspace),
            )
            response = await session.send(TASK_MESSAGE)
            (folder / "response.txt").write_text(response)
        finally:
            if session is not None:
                trace = await session.finish()
        for name, digest in hashes.items():
            path = workspace / name
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError(f"Task Agent modified fixed input: {name}")
        source = workspace / "src/model.js"
        if source.is_symlink() or source.parent.is_symlink() or not source.is_file():
            raise ValueError("Task output is missing or a symlink")
        result = TaskResult(
            await self.content.put_bytes(source.read_bytes(), media_type="text/javascript"), trace
        )
        write_json(folder / "task-result.json", result, immutable=True)
        self.emit(f"Task Agent finished: {task.task_id}; output={folder}")
        return result


async def prepare_environment():
    """Validate the fixed local runtime before planning or making model calls."""
    template = DATASET / "viewer-template"
    if not (DATASET / "snapshot.json").is_file() or not template.is_dir():
        raise ValueError(f"Prepare the local template and snapshot under {DATASET} first")
    snapshot = json.loads((DATASET / "snapshot.json").read_text())
    for name, digest in snapshot["files"].items():
        if hashlib.sha256((template / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"Fixed template changed: {name}")
    prepared = DATASET / "runtime" / ENVIRONMENT_VERSION
    if not prepared.exists():
        raise ValueError(f"Prepared Contract V2 runtime is missing: {prepared}")
    environment = ModelEnvironment(template, prepared)
    environment.check()
    # The prepared runtime is checked before any model call; generated files are
    # validated independently after each Task Agent run.
    return template, snapshot, environment


def main():
    parser = argparse.ArgumentParser(description="3D task environment check and adapter")
    parser.add_argument("--check", action="store_true", required=True)
    parser.parse_args()
    asyncio.run(prepare_environment())
    print("3D environment ready; use this task adapter from a ResearchConfig execution command.")


if __name__ == "__main__":
    main()
