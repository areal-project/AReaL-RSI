"""Core type contracts for AReaL-RSI.

The MVP keeps payloads dictionary-shaped so adapters can evolve with AReaL and
EnvArena without forcing early, brittle dataclass schemas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, TypeAlias

TrainMethod: TypeAlias = Literal["sft", "opd"]
TrajSource: TypeAlias = Literal["eval", "research_validation", "opd"]
EvalAssetConfigType: TypeAlias = Literal["new", "ref"]

ModelRef: TypeAlias = dict[str, Any]
EvalJob: TypeAlias = dict[str, Any]
EvalAssetConfig: TypeAlias = Mapping[str, Any]
Traj: TypeAlias = dict[str, Any]
Dataset: TypeAlias = dict[str, Any]
TrainRun: TypeAlias = dict[str, Any]
MemoryContext: TypeAlias = dict[str, Any]
Metadata: TypeAlias = dict[str, Any]


@dataclass(frozen=True, slots=True)
class EvalRequest:
    """Structured evaluation submission shared by all environment clients.

    ``model``, ``dataset``, ``harness``, ``skill``, and ``reward`` are asset
    configs. Each config must contain ``type``:

    - ``{"type": "ref", ...}`` means use an existing Arena asset reference.
    - ``{"type": "new", ...}`` means the client should create/register the
      asset from the remaining fields before submitting the evaluation.

    Concrete clients validate which fields they need. The common fields for a
    registry-backed asset are ``ref`` or ``{key, version}`` for refs and
    ``key``, ``version``, ``name``, ``spec``, optional ``tags``, optional
    ``metadata``, and optional ``publish`` for new registry assets. New model
    configs may instead provide ``model_path`` when the environment client
    should start a model instance for evaluation.
    """

    model: EvalAssetConfig
    dataset: EvalAssetConfig
    harness: EvalAssetConfig
    skill: EvalAssetConfig | None = None
    reward: EvalAssetConfig | None = None
    job_config: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
