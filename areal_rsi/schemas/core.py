"""Core type contracts for AReaL-RSI.

The MVP keeps payloads dictionary-shaped so adapters can evolve with AReaL and
EnvArena without forcing early, brittle dataclass schemas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, TypeAlias

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


@dataclass(frozen=True, slots=True)
class EvalRequest:
    """Structured evaluation submission shared by all environment clients.

    ``task_set`` and ``harness_config`` remain platform-neutral at this layer.
    Concrete clients validate and encode them for their own evaluation API.
    """

    model_ref: ModelRef
    task_set: str
    harness_config: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
