"""Reuse AReaL's TIR rollout; record generated segments and actual tool I/O."""

from __future__ import annotations

import copy
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from areal import workflow_context
from areal.api import ModelRequest
from tir_workflow import TIRWorkflow

from areal_rsi.api.records import EvaluationSample, TraceEvent
from areal_rsi.infra.artifacts import write_record

_events: ContextVar[list[TraceEvent]] = ContextVar("tir_trace")


class RecordedTIRWorkflow(TIRWorkflow):
    def __init__(self, *, trace_directory: str | None = None, **kwargs: Any):
        super().__init__(**kwargs)
        self.trace_directory = Path(trace_directory) if trace_directory else None

    async def arun_episode(self, engine: Any, data: dict[str, Any]) -> dict[str, Any]:
        events: list[TraceEvent] = []
        token = _events.set(events)
        try:
            result = await super().arun_episode(engine, copy.deepcopy(data))
            if self.trace_directory is not None:
                output = "".join(event.content for event in events if event.kind == "generation")
                score = float(result["rewards"].item())
                calls = sum(event.kind == "tool_call" for event in events)
                sample = EvaluationSample(
                    data["task_id"],
                    output,
                    score,
                    score == 1.0,
                    tuple(events),
                    {"tool_calls": float(calls)},
                )
                sample_index = workflow_context.get().sample_idx or 0
                write_record(self.trace_directory / f"{sample.task_id}-{sample_index}.json", sample)
            return result
        finally:
            _events.reset(token)

    async def _generate_response(
        self, engine: Any, input_ids: Any, max_len: int, waiting_for_tool_start: bool
    ) -> Any:
        events = _events.get()
        if not events:
            events.append(TraceEvent("prompt", self.tokenizer.decode(input_ids)))
        markers = self.start_markers if waiting_for_tool_start else self.end_markers
        generation = self.gconfig.new(
            n_samples=1,
            stop=list(markers),
            max_new_tokens=min(self.gconfig.max_new_tokens, max_len - len(input_ids)),
        )
        response = await engine.agenerate(
            ModelRequest(
                rid=uuid.uuid4().hex,
                input_ids=input_ids,
                gconfig=generation,
                tokenizer=self.tokenizer,
            )
        )
        if not response.output_tokens:
            raise RuntimeError(
                "TIR generation returned no tokens before reaching its context budget"
            )
        events.append(TraceEvent("generation", self.tokenizer.decode(response.output_tokens)))
        return response, response.stop_reason

    async def _execute_tools(self, text: str, tool_manager: Any = None) -> Any:
        _events.get().append(TraceEvent("tool_call", text))
        result, status = await super()._execute_tools(text, tool_manager)
        _events.get().append(TraceEvent("tool_result", str(result)))
        return result, status
