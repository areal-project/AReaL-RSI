"""API is independent of infra; core research infrastructure is independent of UI."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dependency_boundaries() -> None:
    forbidden = {
        "api": ("infra", "ui"),
        "infra": ("ui",),
        "infra.storage": ("infra.coordination", "infra.runtime", "infra.research", "infra.tasks"),
        "infra.execution": ("infra.coordination", "infra.runtime", "infra.research", "infra.tasks"),
        "infra.runtime": ("infra.research",),
        "infra.tasks": ("infra.coordination", "infra.research", "infra.adapters"),
        "infra.coordination": ("infra.research",),
    }
    errors = []
    source = ROOT / "src/areal_rsi"
    for path in source.rglob("*.py"):
        parts = path.relative_to(source).with_suffix("").parts
        package = ["areal_rsi", *parts[:-1]]
        for node in ast.walk(ast.parse(path.read_text())):
            targets = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                prefix = node.module or ""
                if node.level:
                    prefix = ".".join(
                        [*package[:len(package) - node.level + 1], prefix]
                    ).rstrip(".")
                targets = [prefix, *(prefix + "." + alias.name for alias in node.names)]
            for target in targets:
                for layer, denied in forbidden.items():
                    module = ".".join(parts)
                    if (module == layer or module.startswith(layer + ".")) and any(
                        target == "areal_rsi." + name
                        or target.startswith("areal_rsi." + name + ".")
                        for name in denied
                    ):
                        errors.append(f"{path}: {target}")
    assert not errors, "\n".join(errors)

