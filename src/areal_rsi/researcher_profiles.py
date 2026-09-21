"""Named, Markdown-backed researcher identities, resolved before a run is created."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]


def catalog(directory: str | Path | None = None) -> dict[str, Path]:
    roots = [Path(str(files("areal_rsi").joinpath("resources/researcher_profiles")))]
    if directory is not None:
        custom = Path(directory).expanduser()
        if not custom.is_dir():
            raise ValueError(f"Researcher profile directory does not exist: {custom}")
        roots.append(custom)
    return {
        path.relative_to(root).with_suffix("").as_posix(): path
        for root in roots
        for path in sorted(root.rglob("*.md"))
    }


def load_profiles(
    identifiers: Sequence[str], directory: str | Path | None = None
) -> list[dict[str, Any]]:
    assets = catalog(directory)
    profiles = []
    for identifier in identifiers:
        if identifier not in assets:
            raise ValueError(f"Unknown researcher profile: {identifier}")
        path = assets[identifier]
        text = path.read_text(encoding="utf-8")
        parts = text.split("---", 2)
        if len(parts) != 3 or parts[0].strip():
            raise ValueError(f"Profile needs YAML frontmatter: {path}")
        header = yaml.safe_load(parts[1])
        if (
            not isinstance(header, dict)
            or any(
                not isinstance(header.get(key), str) or not header[key].strip()
                for key in ("name", "reminder")
            )
            or not parts[2].strip()
        ):
            raise ValueError(f"Profile needs name, reminder and a prompt: {path}")
        profiles.append(
            {
                "id": identifier,
                "name": header["name"].strip(),
                "reminder": header["reminder"].strip(),
                "prompt": parts[2].strip() + "\n",
            }
        )
    return profiles


def main() -> None:
    parser = argparse.ArgumentParser(description="List available researcher profile assets")
    parser.add_argument("command", choices=["list"])
    parser.add_argument("--directory", type=Path, help="Additional profile asset directory")
    args = parser.parse_args()
    profiles = load_profiles(sorted(catalog(args.directory)), args.directory)
    print(
        json.dumps(
            [{k: p[k] for k in ("id", "name")} for p in profiles], ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()
