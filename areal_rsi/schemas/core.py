"""Core type contracts for AReaL-RSI.

The MVP keeps payloads dictionary-shaped so adapters can evolve with AReaL and
EnvArena without forcing early, brittle dataclass schemas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence, TypeAlias

TrainMethod: TypeAlias = Literal["sft", "opd"]
TrajSource: TypeAlias = Literal["eval", "research_validation", "opd"]
EvalAssetConfigType: TypeAlias = Literal["new", "ref"]

ModelRef: TypeAlias = dict[str, Any]
EvalJob: TypeAlias = dict[str, Any]
EvalAssetConfig: TypeAlias = Mapping[str, Any]
EvalRegistryRef: TypeAlias = str | Mapping[str, Any]
EnvArenaModel: TypeAlias = Mapping[str, Any]
Traj: TypeAlias = dict[str, Any]
Dataset: TypeAlias = dict[str, Any]
TrainRun: TypeAlias = dict[str, Any]
MemoryContext: TypeAlias = dict[str, Any]
Metadata: TypeAlias = dict[str, Any]


@dataclass(frozen=True, slots=True)
class EvalRequest:
    """Structured evaluation submission shared by all environment clients.

    ``model`` and ``skill`` are asset configs. Each config must contain
    ``type``:

    - ``{"type": "ref", ...}`` means use an existing Arena asset reference.
    - ``{"type": "new", ...}`` means the client should create/register the
      asset from the remaining fields before submitting the evaluation.

    ``benchmark`` and ``harness`` are existing Arena refs only, expressed as
    ``"key@version"``, ``{key, version}``, or ``{"ref": ...}``.
    ``benchmark`` is the evaluation task set.

    Concrete clients validate which fields they need. New skill configs use
    ``key``, ``version``, ``name``, ``spec``, optional ``tags``, optional
    ``metadata``, and optional ``publish``. New model configs may instead
    provide ``model_path`` when the environment client should start a model
    instance for evaluation.
    """

    model: EvalAssetConfig
    benchmark: EvalRegistryRef
    harness: EvalRegistryRef
    skill: EvalAssetConfig | None = None
    job_config: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EnvArenaEvalRequest:
    """EnvArena stream-oriented evaluation request.

    ``model`` is a strict JSON object. ``{"type": "ref", "ref": ...}`` points to
    an existing model registry version. ``{"type": "ckpt", "model_path": ...,
    "served_model_name": ..., "base_model_id": ...}`` creates/registers a model
    from a checkpoint.

    ``skills`` is a JSON-list-style sequence of objects. Each item uses
    ``data_id`` to bind to a stream data item. ``type="experience"`` uses
    ``content`` for the experience text/config; ``type="ref"`` uses ``ref`` for
    the existing skill registry ref.

    ``rewards`` follows the same per-data list shape. Each item uses ``data_id``
    and ``ref`` for the reward registry ref.
    """

    model: EnvArenaModel
    stream_id: str
    data_ids: Sequence[str] = field(default_factory=tuple)
    skills: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    rewards: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    job_config: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
