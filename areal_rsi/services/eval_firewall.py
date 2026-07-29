"""Evaluation firewall contract."""

from __future__ import annotations

from typing import Any, Protocol


class EvalFirewall(Protocol):
    """Produces safe evaluation summaries and checkpoint decisions."""

    def summarize(self, eval_result: dict[str, Any], *, policy: dict[str, Any] | None = None) -> dict[str, Any]:
        """Convert raw eval result into a safe report for RSIAgent or ResearchAgent."""

    def decide(self, baseline: dict[str, Any], candidate: dict[str, Any], gate: dict[str, Any]) -> dict[str, Any]:
        """Return accept/reject decision and risk notes from eval metrics."""
