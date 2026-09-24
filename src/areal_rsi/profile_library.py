"""Small shared persona library: bundled defaults plus atomic, audited revisions."""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
from importlib.resources import files
from pathlib import Path
from typing import Any

from areal_rsi.base.storage import now, safe_relative, write_json
from areal_rsi.coordination.profile_resources import load_resources, prepare_profiles
from areal_rsi.researcher_profiles import load_profiles

DEFAULT_IDS = ("general", "kernelbench/algorithm", "kernelbench/memory", "kernelbench/execution")


def digest(profile: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(profile, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def defaults() -> dict[str, dict[str, Any]]:
    root = Path(str(files("areal_rsi").joinpath("resources/profile_library")))
    result = {}
    for profile in load_profiles(DEFAULT_IDS):
        profile.update(load_resources(root / profile["id"]))
        profile["resources"]["AGENTS.md"] = profile["prompt"]
        result[profile["id"]] = profile
    return result


def load_library(directory: Path) -> dict[str, dict[str, Any]]:
    library = defaults()
    path = directory / "library.json"
    if path.exists():
        library.update(json.loads(path.read_text())["profiles"])
    # Validate before any agent is started; preserve the editable source prompt.
    prepare_profiles(list(library.values()))
    return library


def revise(
    library: dict[str, dict[str, Any]], updates: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    revised = {}
    for update in updates:
        identifier = update["profile_id"]
        if identifier not in library or identifier in revised:
            raise ValueError("更新必须指向本次使用的人设，且每个人设只出现一次")
        if not update["reason"].strip() or not update["evidence"] or not update["files"]:
            raise ValueError("人设更新需要理由、原始证据和修改文件")
        profile = copy.deepcopy(library[identifier])
        paths = set()
        for item in update["files"]:
            path = safe_relative(item["path"])
            name = path.as_posix()
            if name != "AGENTS.md" and not (
                len(path.parts) > 1
                and path.parts[0] in {"knowledge", "skills"}
                and path.suffix in {".md", ".py"}
            ):
                raise ValueError("仅可更新 AGENTS.md、knowledge/ 或 skills/ 下的 Markdown/Python")
            if name in paths or not isinstance(item["content"], str) or not item["content"].strip():
                raise ValueError("修改文件不能重复或为空")
            paths.add(name)
            profile["resources"][name] = item["content"]
        if not update["reminder"].strip():
            raise ValueError("reminder 不能为空")
        profile["reminder"] = update["reminder"]
        profile["prompt"] = profile["resources"]["AGENTS.md"]
        profile["required_reading"] = [
            "AGENTS.md",
            "knowledge/core.md",
            *sorted(p for p in profile["resources"] if p.endswith("/SKILL.md")),
        ]
        prepare_profiles([profile])
        revised[identifier] = profile
    return revised


def publish(
    directory: Path,
    run_id: str,
    base: dict[str, dict[str, Any]],
    revised: dict[str, dict[str, Any]],
    review: dict[str, Any],
) -> dict[str, Any]:
    """One atomic commit; stale concurrent reviews are retained without overwriting newer work."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = directory / "library.json"
        data = json.loads(path.read_text()) if path.exists() else {"profiles": {}, "reviews": {}}
        if run_id in data["reviews"]:
            return dict(data["reviews"][run_id])
        current = load_library(directory)
        conflicts = [p for p in revised if digest(current[p]) != digest(base[p])]
        result = {
            **review,
            "status": "conflict" if conflicts else "completed",
            "conflicts": conflicts,
            "published": [] if conflicts else list(revised),
            "finished_at": now(),
        }
        if not conflicts:
            data["profiles"].update(revised)
        data["reviews"][run_id] = result
        write_json(path, data)
        return result
