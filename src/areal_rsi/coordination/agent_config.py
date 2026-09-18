"""Run-frozen research instructions, separate from candidate workspaces."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from areal_rsi.base.storage import decode, json_bytes, safe_relative


@dataclass(slots=True)
class MethodSnapshot:
    prompt: str = ""
    skills: dict[str, str] = field(default_factory=dict)
    sha256: str = ""

    def digest(self) -> str:
        return hashlib.sha256(
            json_bytes(
                {
                    "prompt": self.prompt,
                    "skills": self.skills,
                }
            )
        ).hexdigest()


@dataclass(slots=True)
class AgentConfig:
    base: str = "default"
    prompt: str | None = None
    skills: str | None = None
    snapshot: MethodSnapshot | None = None

    def __post_init__(self) -> None:
        if self.base != "default":
            raise ValueError("Agent base currently supports only default")
        for source in (self.prompt, self.skills):
            if source is not None and (not isinstance(source, str) or not source.strip()):
                raise ValueError("Research method paths must be nonempty strings")

    def freeze(self, role: str = "research", default_prompt: str | None = None) -> None:
        if self.snapshot is not None:
            if self.snapshot.sha256 != self.snapshot.digest():
                raise ValueError("Research method snapshot digest mismatch")
            return
        for source in (self.prompt, self.skills):
            if source and not Path(source).is_absolute():
                raise ValueError("Direct API research method paths must be absolute")
        from areal_rsi.agents.research.prompt import PROMPT as RESEARCH_PROMPT
        from areal_rsi.agents.rsi.prompt import PROMPT as RSI_PROMPT

        result = MethodSnapshot(
            prompt=default_prompt
            if default_prompt is not None
            else {"research": RESEARCH_PROMPT, "rsi": RSI_PROMPT}[role]
        )
        total = 0

        def read(path: Path) -> str:
            nonlocal total
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"Research method requires a regular file: {path}")
            total += path.stat().st_size
            if total > 1_000_000:
                raise ValueError("Research method exceeds the 1 MB text limit")
            return path.read_text(encoding="utf-8")

        if self.prompt:
            result.prompt = read(Path(self.prompt))
            if not result.prompt.strip():
                raise ValueError("Research prompt must not be empty")
        if self.skills:
            root = Path(self.skills)
            if root.is_symlink() or not root.is_dir():
                raise ValueError(f"Research skills directory does not exist: {root}")
            for path in sorted(root.rglob("*")):
                if path.is_symlink():
                    raise ValueError(f"Research skills cannot contain symlinks: {path}")
                if path.is_file():
                    name = path.relative_to(root).as_posix()
                    safe_relative(name)
                    result.skills[name] = read(path)
                    if len(result.skills) > 128:
                        raise ValueError("Research skills exceed the 128 file limit")
            if not result.skills:
                raise ValueError("Research skills directory must not be empty")
        result.sha256 = result.digest()
        self.snapshot = result


@dataclass(slots=True)
class AgentOverrides:
    research: AgentConfig | None = None
    rsi: AgentConfig | None = None

    def freeze(self, defaults: dict[str, str] | None = None) -> None:
        for role in ("research", "rsi"):
            agent = getattr(self, role)
            if agent is not None:
                agent.freeze(role, (defaults or {}).get(role))


def run_method(run_root: Path, role: str = "research") -> MethodSnapshot | None:
    path = run_root / "coordination/config.json"
    if not path.exists():
        return None
    method = json.loads(path.read_text()).get("agents", {}).get(role)
    if not method:
        return None
    snapshot = method.get("snapshot")
    if snapshot is None:
        raise ValueError("Research method was not frozen before starting this run")
    result: MethodSnapshot = decode(MethodSnapshot, snapshot)
    if result.sha256 != result.digest():
        raise ValueError("Research method snapshot digest mismatch")
    return result


def research_prompt(default: str, method: MethodSnapshot | None) -> str:
    if method is None:
        return default
    prompt = method.prompt or default
    if method.skills:
        prompt += (
            "\n\n本次实验为你的角色提供以下 skills。按任务需要用 read_agent_skill(path)"
            "读取入口及其引用的文件。这些是研究方法资料，不属于候选产物；工具只读取文本，"
            "不会自动执行其中的脚本。文件路径：\n" + "\n".join(sorted(method.skills))
        )
    return prompt
