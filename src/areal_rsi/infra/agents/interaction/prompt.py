"""Interaction workflow is maintained as a bundled, reusable skill."""

from importlib.resources import files

from areal_rsi.infra.agents.communication import PROMPT as COMMUNICATION

SKILL = (files("areal_rsi").joinpath("resources/skills/rsi-intake/SKILL.md")
         .read_text(encoding="utf-8"))
PROMPT = (
    "你是 Interaction Agent，负责与用户沟通，将明确的任务交给 RSI。\n\n"
    + SKILL.split("---", 2)[2].strip()
    + "\n\n"
    + COMMUNICATION
)
