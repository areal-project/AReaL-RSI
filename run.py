"""Run from the repository with: uv run --env-file .env python run.py --type web|demo."""

from areal_rsi.ui.launch import main

if __name__ == "__main__":
    raise SystemExit(main())
