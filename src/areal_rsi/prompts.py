"""Read packaged Markdown prompts without changing whitespace or deferred fields."""

from __future__ import annotations

import re
from importlib.resources import files


def load_prompt(path: str, **values: str) -> str:
    """Paths are relative to areal_rsi; replace only explicitly supplied {{fields}}."""
    text = files("areal_rsi").joinpath(path).read_text(encoding="utf-8")
    return re.sub(r"\{\{(\w+)\}\}", lambda match: values.get(match[1], match[0]), text)
