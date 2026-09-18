"""Compatibility command; preflight now checks the production native runtime.

Prefer: uv run --env-file .env --locked rsi preflight [--backend standard|codex]
"""

from areal_rsi.ui.cli import main

if __name__ == "__main__":
    import sys

    sys.argv[1:1] = ["preflight"]
    main()
