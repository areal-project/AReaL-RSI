"""Run-owned paths and records, with read compatibility for the original layout."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import quote, unquote, urlsplit

from areal_rsi.api.base import ExperimentRun, RecordRef
from areal_rsi.base.storage import FileRecords, decode, encode, safe_name, safe_relative, write_json

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
    def legacy_state(self) -> Path:
        return self.data_root / "run-states" / f"{self.run_id}.json"

    @property
    def legacy(self) -> bool:
        return self.legacy_state.exists()

    @property
    def state(self) -> Path:
        return self.root / "run.json"

    @property
    def runtime(self) -> Path:
        return self.root / "runtime"

    @property
    def workflow(self) -> Path:
        # Existing checkpoint locations are also part of task execution keys.
        return self.root if self.legacy else self.root / "workflow"

    @property
    def execution_lock(self) -> Path:
        # Keep the same inode as old executors even after converting run.json.
        if self.legacy:
            return self.data_root / "locks" / f"{self.run_id}.lock"
        return self.runtime / "execution.lock"

    def worker_file(self, suffix: str) -> Path:
        names = {
            "state": "worker.json",
            "lock": "worker.lock",
            "log": "worker.log",
            "stop": "stop.json",
        }
        if self.legacy:
            old = {"state": ".json", "lock": ".lock", "log": ".log", "stop": "-stop.json"}
            return self.data_root / "workers" / f"{self.run_id}{old[suffix]}"
        return self.runtime / names[suffix]

    def agent(self, agent_id: str) -> Path:
        return self.root / "agents" / safe_name(agent_id)

    def iteration(self, iteration_id: str) -> Path:
        return self.root / "iterations" / f"{safe_name(iteration_id)}.json"

    def reference(self, path: Path) -> RecordRef:
        relative = path.relative_to(self.root).as_posix()
        safe_relative(relative)
        return RecordRef(f"run://{self.run_id}/{quote(relative, safe='/')}")


class FileRunStore(FileRecords):
    """Persist nodes and agents once; run.json contains their IDs.

    get/list reconstruct the public in-memory objects. Read-only inspection does
    not migrate files. On the next save, legacy records are written in the new
    format while their original files remain available for provenance/recovery.
    """

    def __init__(self, data_root: Path):
        self.data_root = data_root.resolve()
        self.root = self.data_root / "runs"

    def paths(self, run_id: str) -> RunPaths:
        return RunPaths(self.data_root, safe_name(run_id))

    def state_path(self, run_id: str) -> Path:
        paths = self.paths(run_id)
        return paths.state if paths.state.exists() else paths.legacy_state

    def ids(self) -> list[str]:
        identifiers = {p.parent.name for p in self.root.glob("*/run.json")}
        identifiers.update(p.stem for p in (self.data_root / "run-states").glob("*.json"))
        return sorted(safe_name(identifier) for identifier in identifiers)

    def save(self, key: str, value: Any, *, immutable: bool = True) -> None:
        if not isinstance(value, ExperimentRun) or value.run_id != key:
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
            previous = self.get(key, ExperimentRun)
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
        if kind is not ExperimentRun:
            raise TypeError("FileRunStore only stores ExperimentRun records")
        paths = self.paths(key)
        path = self.state_path(key)
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        if data["run_id"] != key:
            raise ValueError("Run record identity does not match its path")
        if path == paths.legacy_state:
            data = self._legacy_run(data)
        else:
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

    @staticmethod
    def _legacy_run(data: dict[str, Any]) -> dict[str, Any]:
        run_id = data["run_id"]
        states = {"planned": "created", "succeeded": "stopped", "cancelled": "paused"}
        for agent_index, agent in enumerate(data["research_agents"]):
            agent["state"] = states.get(agent["state"], agent["state"])
            if agent.get("error") == "RSI Agent 已停止此研究线":
                agent["state"] = "stopped"
            nodes = []
            parent = None
            for index, old in enumerate(agent["iterations"]):
                identifier = f"{agent['agent_id']}-{old['iteration']}"
                nodes.append(
                    {
                        "iteration_id": identifier,
                        "run_id": run_id,
                        "agent_id": agent["agent_id"],
                        "assignment_ref": {"uri": f"legacy-run://{run_id}#/plan"},
                        "state": "completed",
                        "parent_iteration_id": parent,
                        "report_ref": {
                            "uri": (
                                f"legacy-run://{run_id}#/research_agents/{agent_index}/iterations/{index}"
                            )
                        },
                        "created_at": old["started_at"],
                        "updated_at": old["finished_at"],
                    }
                )
                parent = identifier
            agent["iterations"] = nodes
        return data

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
        elif uri.scheme == "legacy-run":
            if uri.path:
                raise ValueError("Invalid legacy Run reference")
            path = self.paths(uri.netloc).legacy_state
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
