"""API is independent of infra; core research infrastructure is independent of UI."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dependency_and_state_boundaries() -> None:
    forbidden = {
        "api": ("infra", "ui"),
        "infra": ("ui",),
        "infra.coordination": ("infra.research",),
    }
    errors = []
    source = ROOT / "src/areal_rsi"
    for path in source.rglob("*.py"):
        parts = path.relative_to(source).with_suffix("").parts
        package = ["areal_rsi", *parts[:-1]]
        for node in ast.walk(ast.parse(path.read_text())):
            # Coordinator state is a data contract, never an untyped JSON bag.
            if parts[:2] == ("infra", "coordination") or parts == ("infra", "research"):
                owner = None
                if isinstance(node, ast.Subscript):
                    owner = node.value
                elif (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"get", "setdefault", "update", "pop"}
                ):
                    owner = node.func.value
                if isinstance(owner, ast.Attribute) and owner.attr == "state":
                    errors.append(f"{path}:{owner.lineno}: use typed coordination state fields")
            targets = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                prefix = node.module or ""
                if node.level:
                    prefix = ".".join([*package[: len(package) - node.level + 1], prefix]).rstrip(
                        "."
                    )
                targets = [prefix, *(prefix + "." + alias.name for alias in node.names)]
            for target in targets:
                if parts[0] in {"api", "infra"} and (
                    target == "examples" or target.startswith("examples.")
                ):
                    errors.append(f"{path}: core cannot import task example {target}")
                for layer, denied in forbidden.items():
                    module = ".".join(parts)
                    if (module == layer or module.startswith(layer + ".")) and any(
                        target == "areal_rsi." + name
                        or target.startswith("areal_rsi." + name + ".")
                        for name in denied
                    ):
                        errors.append(f"{path}: {target}")
    assert not errors, "\n".join(errors)
