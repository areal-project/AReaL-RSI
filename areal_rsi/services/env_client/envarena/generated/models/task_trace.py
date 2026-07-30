from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.artifact_ref import ArtifactRef
    from ..models.task import Task
    from ..models.task_event import TaskEvent
    from ..models.task_trace_reward_type_0 import TaskTraceRewardType0
    from ..models.task_trace_span_query import TaskTraceSpanQuery
    from ..models.trace_span import TraceSpan


T = TypeVar("T", bound="TaskTrace")


@_attrs_define
class TaskTrace:
    """
    Attributes:
        events (list[TaskEvent]):
        task (None | Task | Unset):
        reward (None | TaskTraceRewardType0 | Unset):
        trace_id (str | Unset):
        span_query (TaskTraceSpanQuery | Unset):
        spans (list[TraceSpan] | Unset):
        artifacts (list[ArtifactRef] | Unset):
    """

    events: list[TaskEvent]
    task: None | Task | Unset = UNSET
    reward: None | TaskTraceRewardType0 | Unset = UNSET
    trace_id: str | Unset = UNSET
    span_query: TaskTraceSpanQuery | Unset = UNSET
    spans: list[TraceSpan] | Unset = UNSET
    artifacts: list[ArtifactRef] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.task import Task
        from ..models.task_trace_reward_type_0 import TaskTraceRewardType0

        events = []
        for events_item_data in self.events:
            events_item = events_item_data.to_dict()
            events.append(events_item)

        task: dict[str, Any] | None | Unset
        if isinstance(self.task, Unset):
            task = UNSET
        elif isinstance(self.task, Task):
            task = self.task.to_dict()
        else:
            task = self.task

        reward: dict[str, Any] | None | Unset
        if isinstance(self.reward, Unset):
            reward = UNSET
        elif isinstance(self.reward, TaskTraceRewardType0):
            reward = self.reward.to_dict()
        else:
            reward = self.reward

        trace_id = self.trace_id

        span_query: dict[str, Any] | Unset = UNSET
        if not isinstance(self.span_query, Unset):
            span_query = self.span_query.to_dict()

        spans: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.spans, Unset):
            spans = []
            for spans_item_data in self.spans:
                spans_item = spans_item_data.to_dict()
                spans.append(spans_item)

        artifacts: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.artifacts, Unset):
            artifacts = []
            for artifacts_item_data in self.artifacts:
                artifacts_item = artifacts_item_data.to_dict()
                artifacts.append(artifacts_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "events": events,
            }
        )
        if task is not UNSET:
            field_dict["task"] = task
        if reward is not UNSET:
            field_dict["reward"] = reward
        if trace_id is not UNSET:
            field_dict["trace_id"] = trace_id
        if span_query is not UNSET:
            field_dict["span_query"] = span_query
        if spans is not UNSET:
            field_dict["spans"] = spans
        if artifacts is not UNSET:
            field_dict["artifacts"] = artifacts

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.artifact_ref import ArtifactRef
        from ..models.task import Task
        from ..models.task_event import TaskEvent
        from ..models.task_trace_reward_type_0 import TaskTraceRewardType0
        from ..models.task_trace_span_query import TaskTraceSpanQuery
        from ..models.trace_span import TraceSpan

        d = dict(src_dict)
        events = []
        _events = d.pop("events")
        for events_item_data in _events:
            events_item = TaskEvent.from_dict(events_item_data)

            events.append(events_item)

        def _parse_task(data: object) -> None | Task | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                task_type_1 = Task.from_dict(data)

                return task_type_1
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | Task | Unset, data)

        task = _parse_task(d.pop("task", UNSET))

        def _parse_reward(data: object) -> None | TaskTraceRewardType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                reward_type_0 = TaskTraceRewardType0.from_dict(data)

                return reward_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | TaskTraceRewardType0 | Unset, data)

        reward = _parse_reward(d.pop("reward", UNSET))

        trace_id = d.pop("trace_id", UNSET)

        _span_query = d.pop("span_query", UNSET)
        span_query: TaskTraceSpanQuery | Unset
        if isinstance(_span_query, Unset):
            span_query = UNSET
        else:
            span_query = TaskTraceSpanQuery.from_dict(_span_query)

        _spans = d.pop("spans", UNSET)
        spans: list[TraceSpan] | Unset = UNSET
        if _spans is not UNSET:
            spans = []
            for spans_item_data in _spans:
                spans_item = TraceSpan.from_dict(spans_item_data)

                spans.append(spans_item)

        _artifacts = d.pop("artifacts", UNSET)
        artifacts: list[ArtifactRef] | Unset = UNSET
        if _artifacts is not UNSET:
            artifacts = []
            for artifacts_item_data in _artifacts:
                artifacts_item = ArtifactRef.from_dict(artifacts_item_data)

                artifacts.append(artifacts_item)

        task_trace = cls(
            events=events,
            task=task,
            reward=reward,
            trace_id=trace_id,
            span_query=span_query,
            spans=spans,
            artifacts=artifacts,
        )

        task_trace.additional_properties = d
        return task_trace

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
