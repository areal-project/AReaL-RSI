"""ResearchSystem run-owned paths, nodes, agents and immutable references."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import quote, unquote, urlsplit

from areal_rsi.api.records import RecordRef, ResearchRun
from areal_rsi.infra.storage.files import (
    FileRecords,
    decode,
    encode,
    safe_name,
    safe_relative,
    write_json,
)

T = TypeVar("T")
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class RunPaths:
    data_root: Path
    run_id: str

    def __post_init__(self) -> None:
        safe_name(self.run_id)

    @property
    def root(self) -> Path:
        return self.data_root / "runs" / self.run_id

    @property
    def state(self) -> Path:
        return self.root / "run.json"

    @property
    def runtime(self) -> Path:
        return self.root / "runtime"

    @property
    def execution_lock(self) -> Path:
        return self.runtime / "execution.lock"

    def agent(self, agent_id: str) -> Path:
        return self.root / "agents" / safe_name(agent_id)

    def iteration(self, iteration_id: str) -> Path:
        return self.root / "iterations" / f"{safe_name(iteration_id)}.json"

    def reference(self, path: Path) -> RecordRef:
        relative = path.relative_to(self.root).as_posix()
        safe_relative(relative)
        return RecordRef(f"run://{self.run_id}/{quote(relative, safe='/')}")


class FileRunStore(FileRecords):
    """Persist nodes and agents once; run.json contains their IDs."""

    def __init__(self, data_root: Path):
        self.data_root = data_root.resolve()
        self.root = self.data_root / "runs"

    def paths(self, run_id: str) -> RunPaths:
        return RunPaths(self.data_root, safe_name(run_id))

    def state_path(self, run_id: str) -> Path:
        return self.paths(run_id).state

    def ids(self) -> list[str]:
        identifiers = {
            p.parent.name for p in self.root.glob("*/run.json")
            if not (p.parent / ".creating").exists()
        }
        return sorted(safe_name(identifier) for identifier in identifiers)

    def save(self, key: str, value: Any, *, immutable: bool = True) -> None:
        if not isinstance(value, ResearchRun) or value.run_id != key:
            raise ValueError("Run record identity does not match its key")
        paths = self.paths(key)
        data = encode(value)
        data["schema_version"] = SCHEMA_VERSION
        agent_ids: list[str] = []
        iteration_ids: set[str] = set()
        for agent in value.research_agents:
            if agent.agent_id in agent_ids:
                raise ValueError("Duplicate Agent identity in Run")
            agent_ids.append(safe_name(agent.agent_id))
            for step in agent.iterations:
                if step.run_id != key or step.agent_id != agent.agent_id:
                    raise ValueError("ResearchIteration belongs to a different Run or Agent")
                if step.iteration_id in iteration_ids:
                    raise ValueError("Duplicate ResearchIteration identity in Run")
                iteration_ids.add(safe_name(step.iteration_id))
        # Validate the immutable creation condition before touching child records.
        if immutable and self.state_path(key).exists():
            previous = self.get(key, ResearchRun)
            if previous != value:
                raise ValueError("禁止覆盖历史 Run 记录")
            return
        for agent in value.research_agents:
            for step in agent.iterations:
                write_json(paths.iteration(step.iteration_id), step)
            agent_data = encode(agent)
            agent_data["iterations"] = [step.iteration_id for step in agent.iterations]
            write_json(paths.agent(agent.agent_id) / "agent.json", agent_data)
        data["research_agents"] = agent_ids
        write_json(paths.state, data, immutable=immutable)

    def get(self, key: str, kind: type[T]) -> T | None:
        if kind is not ResearchRun:
            raise TypeError("FileRunStore only stores ResearchRun records")
        paths = self.paths(key)
        path = self.state_path(key)
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        if data["run_id"] != key:
            raise ValueError("Run record identity does not match its path")
        if data.pop("schema_version") != SCHEMA_VERSION:
            raise ValueError("Unsupported Run schema version")
        agents = []
        seen_agents: set[str] = set()
        seen_steps: set[str] = set()
        for agent_id in data["research_agents"]:
            if agent_id in seen_agents:
                raise ValueError("Duplicate Agent identity in Run")
            seen_agents.add(agent_id)
            agent = json.loads((paths.agent(agent_id) / "agent.json").read_text())
            if agent["agent_id"] != agent_id:
                raise ValueError("Agent record identity does not match its path")
            steps = []
            for identifier in agent["iterations"]:
                step = json.loads(paths.iteration(identifier).read_text())
                if (
                    step["iteration_id"] != identifier
                    or step["run_id"] != key
                    or step["agent_id"] != agent_id
                    or identifier in seen_steps
                ):
                    raise ValueError("ResearchIteration identity does not match its owner")
                seen_steps.add(identifier)
                steps.append(step)
            agent["iterations"] = steps
            agents.append(agent)
        data["research_agents"] = agents
        result: T = decode(kind, data)
        return result

    def list(self, kind: type[T]) -> list[T]:
        return [item for key in self.ids() if (item := self.get(key, kind)) is not None]

    def read_json(self, ref: RecordRef) -> Any:
        """Resolve local records; callers still control which refs an Agent receives."""
        uri = urlsplit(ref.uri)
        if uri.query:
            raise ValueError("Record references do not accept query parameters")
        if uri.scheme == "run":
            directory = self.paths(uri.netloc).root
            path = directory / safe_relative(unquote(uri.path.removeprefix("/")))
            if not path.resolve().is_relative_to(directory.resolve()):
                raise ValueError("Record reference escapes its Run")
        elif uri.scheme == "content":
            if (
                uri.path
                or len(uri.netloc) != 64
                or any(c not in "0123456789abcdef" for c in uri.netloc)
            ):
                raise ValueError("Invalid content reference")
            path = self.data_root / "content" / uri.netloc
            if hashlib.sha256(path.read_bytes()).hexdigest() != uri.netloc:
                raise ValueError("Content digest mismatch")
        else:
            raise ValueError("Unsupported local record reference")
        value = json.loads(path.read_text())
        pointer = unquote(uri.fragment)
        if pointer:
            if not pointer.startswith("/"):
                raise ValueError("Invalid JSON pointer")
            for raw in pointer[1:].split("/"):
                key = raw.replace("~1", "/").replace("~0", "~")
                value = value[int(key)] if isinstance(value, list) else value[key]
        return value
