"""Content-addressed research Skill library and auditable lifecycle."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from areal_rsi.api.goal import SkillVersion
from areal_rsi.base.storage import (
    FileContentStore,
    decode,
    encode,
    now,
    safe_name,
    write_json,
)

MAX_PACKAGE_BYTES = 2_000_000


def skill_configuration() -> dict[str, Any]:
    value = json.loads(os.environ.get("RSI_RESEARCH_SKILLS", "{}"))
    if not isinstance(value, dict) or set(value) - {
        "library_root",
        "paths",
        "selection",
        "capture_candidates",
    }:
        raise ValueError("Invalid RSI_RESEARCH_SKILLS configuration")
    if value.get("selection", "explicit") != "explicit":
        raise ValueError("Only explicit Skill selection is supported")
    paths = value.get("paths", [])
    if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths):
        raise ValueError("Skill paths must be an array of paths")
    return value


class SkillLoader:
    def __init__(self, content: FileContentStore):
        self.content = content

    async def freeze(self, source: Path) -> SkillVersion:
        source = source.expanduser().resolve(strict=True)
        text = (source / "SKILL.md").read_text()
        if not text.startswith("---\n") or "\n---\n" not in text[4:]:
            raise ValueError("SKILL.md requires YAML front matter")
        meta = yaml.safe_load(text.split("---", 2)[1])
        if not isinstance(meta, dict) or any(
            not isinstance(meta.get(key), str) or not meta[key].strip()
            for key in ("name", "version", "description", "scope")
        ):
            raise ValueError("Skill requires name, version, description and scope")
        safe_name(meta["name"])
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", meta["name"]):
            raise ValueError("DSH Skill names must use lowercase kebab-case")
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", meta["version"]):
            raise ValueError("Skill version must use major.minor.patch")
        dependencies = meta.get("dependencies", [])
        if not isinstance(dependencies, list) or any(not isinstance(x, str) for x in dependencies):
            raise ValueError("Skill dependencies must be strings")
        size = 0
        for file in source.rglob("*"):
            if file.is_symlink():
                raise ValueError("Skill packages cannot contain symlinks")
            if not file.is_file():
                continue
            relative = file.relative_to(source)
            if relative.parts[0] not in {"SKILL.md", "scripts", "references", "templates"}:
                raise ValueError(f"Unexpected Skill resource: {relative}")
            if any(part.lower() in {".env", "credentials", "auth.json"} for part in relative.parts):
                raise ValueError("Authentication resources cannot be packaged")
            data = file.read_bytes()
            if re.search(rb"sk-[A-Za-z0-9]{20,}|-----BEGIN .*PRIVATE KEY", data):
                raise ValueError("Credential-like content in Skill resource")
            size += len(data)
            if size > MAX_PACKAGE_BYTES:
                raise ValueError("Skill package exceeds 2 MB")
        return SkillVersion(
            meta["name"],
            meta["version"],
            meta["description"],
            meta["scope"],
            tuple(dependencies),
            await self.content.snapshot(source),
        )

    async def materialize(self, skill: SkillVersion, workspace: Path) -> tuple[Path, Any]:
        directory = workspace / "research-skills" / skill.skill_id / skill.version
        await self.content.materialize(skill.content, directory)
        for file in directory.rglob("*"):
            if file.is_file():
                file.chmod(0o444)
        entry = directory / skill.entrypoint
        prompt = await self.content.put_text(
            entry.read_text()
            + "\n\nSkill directory: "
            + str(directory.relative_to(workspace))
            + "\nResolve all relative resources from this directory. "
            "Load this Skill and use a relevant resource before proposing."
        )
        return directory, prompt


class SkillLibrary:
    def __init__(self, root: Path, content: FileContentStore):
        self.root, self.content = root.expanduser().resolve(), content
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, skill_id: str, version: str) -> Path:
        safe_name(skill_id)
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
            raise ValueError("Invalid Skill version")
        return self.root / skill_id / version

    def inspect(self, skill_id: str, version: str) -> dict[str, Any]:
        value: dict[str, Any] = json.loads(
            (self.path(skill_id, version) / "record.json").read_text()
        )
        return value

    def list(self) -> list[dict[str, Any]]:
        return [json.loads(p.read_text()) for p in sorted(self.root.glob("*/*/record.json"))]

    async def add(self, skill: SkillVersion, provenance: dict[str, Any]) -> dict[str, Any]:
        directory = self.path(skill.skill_id, skill.version)
        if directory.exists():
            major, minor, patch = map(int, skill.version.split("."))
            while self.path(skill.skill_id, f"{major}.{minor}.{patch}").exists():
                patch += 1
            version = f"{major}.{minor}.{patch}"
            with tempfile.TemporaryDirectory() as temporary:
                package = Path(temporary) / "package"
                await self.content.materialize(skill.content, package)
                entry = package / "SKILL.md"
                entry.write_text(
                    re.sub(r"(?m)^version:.*$", "version: " + version, entry.read_text(), count=1)
                )
                skill = await SkillLoader(self.content).freeze(package)
            directory = self.path(skill.skill_id, skill.version)
        directory.mkdir(parents=True, exist_ok=True)
        await self.content.materialize(skill.content, directory / "package")
        record = {
            "skill": encode(skill),
            "state": "candidate",
            "created_at": now(),
            "provenance": provenance,
            "validation": None,
            "cross_task_evidence": [],
        }
        write_json(directory / "record.json", record, immutable=True)
        return record

    async def validate(self, skill_id: str, version: str) -> dict[str, Any]:
        record = self.inspect(skill_id, version)
        skill = decode(SkillVersion, record["skill"])
        loaded = await SkillLoader(self.content).freeze(self.path(skill_id, version) / "package")
        if loaded != skill:
            raise ValueError("Skill package changed after capture")
        import shutil

        missing = [d for d in skill.dependencies if shutil.which(d) is None]
        if missing:
            raise ValueError("Missing Skill dependencies: " + ", ".join(missing))
        # Compile scripts without executing untrusted installers or arbitrary code.
        for path in (self.path(skill_id, version) / "package/scripts").glob("*.py"):
            compile(path.read_text(), str(path), "exec")
        validator = self.path(skill_id, version) / "package/scripts/validate.py"
        if not validator.exists():
            raise ValueError(
                "Skill validation requires scripts/validate.py with scenario self-tests"
            )
        from areal_rsi.modules.runtime_package import sandbox_command

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            workspace = directory / "workspace"
            await self.content.materialize(skill.content, workspace / "package")
            command = sandbox_command(
                directory, workspace, (), payload=[sys.executable, "package/scripts/validate.py"]
            )
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=workspace,
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(workspace)},
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), 30)
            except BaseException:
                if process.returncode is None:
                    import signal

                    os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
                raise
            if process.returncode:
                raise ValueError(
                    "Skill scenario validation failed: " + stderr.decode(errors="replace")[:2000]
                )
            resource_evidence = {
                "exit_code": process.returncode,
                "stdout": stdout.decode(errors="replace")[:4000],
                "stderr": stderr.decode(errors="replace")[:1000],
            }
        record.update(
            state="active" if record["state"] == "active" else "validated",
            validation={
                "at": now(),
                "structure": True,
                "dependencies": True,
                "resource_execution": resource_evidence,
                "cross_task_benefit_proven": False,
            },
        )
        write_json(self.path(skill_id, version) / "record.json", record)
        return record

    def activate(self, skill_id: str, version: str) -> dict[str, Any]:
        record = self.inspect(skill_id, version)
        if record["state"] not in {"validated", "active"}:
            raise ValueError("Validate the Skill before explicit activation")
        record.update(state="active", activated_at=now(), activation_source="user-cli")
        write_json(self.path(skill_id, version) / "record.json", record)
        return record

    def deprecate(self, skill_id: str, version: str, reason: str) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("Deprecation requires a reason")
        record = self.inspect(skill_id, version)
        record.update(state="deprecated", deprecated_at=now(), deprecation_reason=reason)
        write_json(self.path(skill_id, version) / "record.json", record)
        return record
