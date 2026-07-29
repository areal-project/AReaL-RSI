"""Core type contracts for AReaL-RSI.

The MVP keeps payloads dictionary-shaped so adapters can evolve with AReaL and
EnvArena without forcing early, brittle dataclass schemas.
"""

from __future__ import annotations

from typing import Any, Literal, TypeAlias

TrainMethod: TypeAlias = Literal["sft", "opd"]
TrajSource: TypeAlias = Literal["eval", "research_validation", "opd"]

Endpoint: TypeAlias = dict[str, Any]
ModelRef: TypeAlias = dict[str, Any]
EvalJob: TypeAlias = dict[str, Any]
Traj: TypeAlias = dict[str, Any]
Dataset: TypeAlias = dict[str, Any]
TrainRun: TypeAlias = dict[str, Any]
MemoryContext: TypeAlias = dict[str, Any]
Metadata: TypeAlias = dict[str, Any]
