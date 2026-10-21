"""Enforce the dependency boundaries used by the core and optional entry points."""

import ast
from importlib.util import resolve_name
from pathlib import Path


def test_dependency_boundaries() -> None:
    forbidden = {
        "api": ("infra", "ui"),
        "infra": ("ui",),
        "infra.session": ("infra.service",),
        "infra.service.handler": ("infra.service.transport",),
        "infra.storage": (
            "infra.execution",
            "infra.coordination",
            "infra.session",
            "infra.service",
        ),
        "infra.execution": ("infra.coordination", "infra.session", "infra.service"),
        "infra.coordination": ("infra.session",),
        "infra.coordination.policies": (
            "infra.coordination.runtime",
            "infra.coordination.store",
            "infra.codex",
            "infra.storage",
            "infra.execution",
        ),
        "infra.coordination.tools": ("infra.coordination.runtime",),
    }
    source = Path(__file__).resolve().parents[1] / "src/areal_rsi"
    for path in source.rglob("*.py"):
        module = ".".join(path.relative_to(source).with_suffix("").parts)
        package = ".".join(("areal_rsi", *path.relative_to(source).parts[:-1]))
        for node in ast.walk(ast.parse(path.read_text())):
            targets = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                prefix = node.module or ""
                if node.level:
                    prefix = resolve_name("." * node.level + prefix, package)
                targets = [prefix, *(prefix + "." + alias.name for alias in node.names)]
            for target in targets:
                if module.startswith(("api.", "infra.")):
                    assert target.split(".")[0] != "examples", (path, target)
                for layer, denied in forbidden.items():
                    if module == layer or module.startswith(layer + "."):
                        assert not any(
                            target == f"areal_rsi.{name}" or target.startswith(f"areal_rsi.{name}.")
                            for name in denied
                        ), (path, target)
