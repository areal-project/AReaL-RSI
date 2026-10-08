#!/usr/bin/env python3
"""Compact mathematical system prompt for a versioned Arena Claude Code Harness."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    # Arena's built-in Claude adapter appends its Harness prompt to the general
    # coding prompt. Math checkpoints need the compact system prompt used by TIR.
    arguments = sys.argv[1:]
    arguments = [
        "--system-prompt" if item == "--append-system-prompt" else item for item in arguments
    ]
    binary = Path(os.environ["ARENA_HARNESS_DIR"]) / "bin/claude-real"
    os.execv(str(binary), [str(binary), *arguments])


if __name__ == "__main__":
    main()
