"""Version the exact TIR prompt and runner code used by training and evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from string import Formatter

from areal_rsi.api.control import decode, json_bytes
from areal_rsi.infra.storage.files import write_bytes

TIR_RUNNER_FILES = (
    "__init__.py",
    "prompts.py",
    "tir_workflow.py",
    "tool_manager.py",
    "tools/__init__.py",
    "tools/base.py",
    "tools/calculator_tool.py",
    "tools/python_tool.py",
)


@dataclass(frozen=True, slots=True)
class TirHarnessSpec:
    harness_id: str
    prompt_digest: str
    runner_digest: str
    max_turns: int
    max_length: int
    tool_timeout: int
    enabled_tools: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.harness_id.strip() or any(
            type(value) is not int or value < 1
            for value in (self.max_turns, self.max_length, self.tool_timeout)
        ):
            raise ValueError("TIR Harness requires an identity and positive execution limits")
        if self.enabled_tools != ("calculator",):
            raise ValueError("First TIR RSI generation permits only the calculator tool")
        for value in (self.prompt_digest, self.runner_digest):
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
                raise ValueError("TIR Harness requires SHA-256 content digests")


def _sha256(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _read_regular(path: Path) -> bytes:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"TIR Harness input must be a regular file: {path}")
    return path.read_bytes()


def validate_tir_prompt(text: str) -> None:
    if not text.strip():
        raise ValueError("TIR Harness prompt is empty")
    try:
        fields = [field for _, field, _, _ in Formatter().parse(text) if field is not None]
    except ValueError as exc:
        raise ValueError("TIR Harness prompt contains malformed braces") from exc
    if fields != ["prompt"]:
        raise ValueError("TIR Harness prompt requires exactly one {prompt} field")


def _runner_digest(root: Path) -> str:
    checkout = root.parents[1]
    package = checkout / "areal"
    if root != checkout / "examples/tir" or root.is_symlink() or package.is_symlink():
        raise ValueError("TIR runner must be the AReaL checkout's examples/tir directory")
    if not root.is_dir() or not package.is_dir():
        raise ValueError("TIR runner requires the AReaL source package")
    _read_regular(package / "__init__.py")
    _read_regular(package / "trainer/rl_trainer.py")
    for name in TIR_RUNNER_FILES:
        _read_regular(root / name)
    python_sources: list[Path] = []
    for source_root in (package, root):
        for path in source_root.rglob("*"):
            if path.is_symlink():
                raise ValueError("AReaL source lock cannot contain symbolic links")
            if path.suffix == ".py" and "tests" not in path.relative_to(source_root).parts:
                python_sources.append(path)
    sources = (
        checkout / "pyproject.toml",
        checkout / "uv.lock",
        *sorted(python_sources),
    )
    digest = hashlib.sha256()
    for path in sources:
        relative = path.relative_to(checkout).as_posix().encode()
        digest.update(relative + b"\0" + hashlib.sha256(_read_regular(path)).digest())
    return "sha256:" + digest.hexdigest()


def _identity(spec: TirHarnessSpec) -> str:
    return (
        "harness_"
        + hashlib.sha256(
            json_bytes(
                {
                    "prompt_digest": spec.prompt_digest,
                    "runner_digest": spec.runner_digest,
                    "max_turns": spec.max_turns,
                    "max_length": spec.max_length,
                    "tool_timeout": spec.tool_timeout,
                    "enabled_tools": spec.enabled_tools,
                }
            )
        ).hexdigest()
    )


def freeze_tir_harness(
    prompt_file: Path,
    runner_root: Path,
    destination: Path,
    *,
    max_turns: int = 2,
    max_length: int = 3000,
    tool_timeout: int = 30,
) -> TirHarnessSpec:
    if destination.exists():
        raise FileExistsError(destination)
    prompt_bytes = _read_regular(prompt_file)
    validate_tir_prompt(prompt_bytes.decode())
    provisional = TirHarnessSpec(
        "pending",
        _sha256(prompt_bytes),
        _runner_digest(runner_root),
        max_turns,
        max_length,
        tool_timeout,
        ("calculator",),
    )
    spec = TirHarnessSpec(
        _identity(provisional),
        provisional.prompt_digest,
        provisional.runner_digest,
        provisional.max_turns,
        provisional.max_length,
        provisional.tool_timeout,
        provisional.enabled_tools,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".tir-harness-") as temporary:
        staged = Path(temporary) / "harness"
        staged.mkdir()
        write_bytes(staged / "prompt.txt", prompt_bytes, immutable=True)
        write_bytes(staged / "manifest.json", json_bytes(spec), immutable=True)
        staged.rename(destination)
    return spec


def load_tir_harness(root: Path, runner_root: Path) -> tuple[TirHarnessSpec, str]:
    manifest_bytes = _read_regular(root / "manifest.json")
    spec: TirHarnessSpec = decode(TirHarnessSpec, json.loads(manifest_bytes))
    if manifest_bytes != json_bytes(spec) or spec.harness_id != _identity(spec):
        raise ValueError("TIR Harness manifest changed")
    prompt_bytes = _read_regular(root / "prompt.txt")
    if spec.prompt_digest != _sha256(prompt_bytes):
        raise ValueError("TIR Harness prompt changed")
    if spec.runner_digest != _runner_digest(runner_root):
        raise ValueError("TIR runner source changed")
    prompt = prompt_bytes.decode()
    validate_tir_prompt(prompt)
    return spec, prompt


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze a TIR prompt and AReaL runner source")
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--runner-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-turns", type=int, default=2)
    parser.add_argument("--max-length", type=int, default=3000)
    parser.add_argument("--tool-timeout", type=int, default=30)
    args = parser.parse_args()
    spec = freeze_tir_harness(
        args.prompt,
        args.runner_root,
        args.output,
        max_turns=args.max_turns,
        max_length=args.max_length,
        tool_timeout=args.tool_timeout,
    )
    print(json.dumps({"harness_id": spec.harness_id, "output": str(args.output.resolve())}))


if __name__ == "__main__":
    main()
