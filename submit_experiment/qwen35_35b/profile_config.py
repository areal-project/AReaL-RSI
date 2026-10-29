"""Configuration composition shared by this model's RL and SFT submitters."""

from __future__ import annotations

import copy
import os
import re
import shlex
from pathlib import Path
from typing import Any

import yaml

SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}\Z")


def load_yaml(path: Path) -> dict[str, Any]:
    """Reject duplicate keys rather than silently losing a configuration value."""

    class UniqueLoader(yaml.SafeLoader):
        pass

    def mapping(loader: Any, node: Any) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node)
            if not isinstance(key, str) or key in result:
                raise ValueError(f"{path}: duplicate or non-string YAML key")
            result[key] = loader.construct_object(value_node)
        return result

    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    value = yaml.load(path.read_text(), Loader=UniqueLoader)
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a YAML mapping")
    return value


def merge(base: Any, patch: Any) -> Any:
    """Deep merge mappings; the single scheduling_spec item is merged by index."""
    if isinstance(base, dict) and isinstance(patch, dict):
        result = copy.deepcopy(base)
        for key, value in patch.items():
            if key == "scheduling_spec" and key in result:
                if not isinstance(value, list) or len(value) != len(result[key]):
                    raise ValueError("scheduling_spec must retain the base worker spec count")
                result[key] = [merge(old, new) for old, new in zip(result[key], value, strict=True)]
            else:
                result[key] = merge(result[key], value) if key in result else copy.deepcopy(value)
        return result
    return copy.deepcopy(patch)


def protect_base(base: Any, patch: Any, prefix: str = "") -> None:
    """Base owns fixed leaves; params can supply new leaves and Slurm resources."""
    if prefix == "launch.slurm":
        return
    if isinstance(base, dict) and isinstance(patch, dict):
        for key, value in patch.items():
            name = f"{prefix}.{key}" if prefix else key
            if key in base:
                protect_base(base[key], value, name)
    elif prefix.endswith("scheduling_spec") and isinstance(base, list) and isinstance(patch, list):
        if len(base) != len(patch):
            raise ValueError("scheduling_spec must retain the base worker spec count")
        for index, (old, new) in enumerate(zip(base, patch, strict=True)):
            protect_base(old, new, f"{prefix}.{index}")
    else:
        raise ValueError(f"{prefix} is fixed in base.yaml; change the base profile deliberately")


def resolve_areal_dir(credentials: str = "") -> str:
    """Read the source path without executing a credentials file as shell code."""
    source = os.environ.get("AREAL_DIR", "").strip()
    relative_to = Path.cwd()
    if not source and credentials:
        path = Path(credentials).expanduser().resolve()
        if path.is_file():
            relative_to = path.parent
            for line in path.read_text().splitlines():
                match = re.fullmatch(r"\s*(?:export\s+)?AREAL_DIR\s*=\s*(.*)", line)
                if match is None:
                    continue
                try:
                    words = shlex.split(match[1], comments=True)
                except ValueError:
                    raise ValueError("AREAL_DIR in credentials.env has invalid quoting") from None
                if len(words) > 1:
                    raise ValueError("AREAL_DIR in credentials.env must be one quoted path")
                source = words[0].strip() if words else ""
    if not source:
        raise ValueError("Set AREAL_DIR in the environment or credentials.env before submitting")
    if "$" in source or "`" in source or "\n" in source or "\r" in source:
        raise ValueError("AREAL_DIR must be a literal path; shell expressions are not supported")
    return str((relative_to / Path(source).expanduser()).resolve())
