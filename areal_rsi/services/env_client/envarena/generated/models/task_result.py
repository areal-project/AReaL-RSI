from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.result_status import ResultStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.task_result_raw import TaskResultRaw
    from ..models.task_result_sub_scores import TaskResultSubScores


T = TypeVar("T", bound="TaskResult")


@_attrs_define
class TaskResult:
    """
    Attributes:
        task_id (str):
        status (ResultStatus):
        computed_at (datetime.datetime):
        score (float | None | Unset):
        sub_scores (TaskResultSubScores | Unset):
        pass_ (bool | Unset):
        raw (TaskResultRaw | Unset):
        artifacts_uri (str | Unset):
        trace_id (str | Unset):
    """

    task_id: str
    status: ResultStatus
    computed_at: datetime.datetime
    score: float | None | Unset = UNSET
    sub_scores: TaskResultSubScores | Unset = UNSET
    pass_: bool | Unset = UNSET
    raw: TaskResultRaw | Unset = UNSET
    artifacts_uri: str | Unset = UNSET
    trace_id: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        task_id = self.task_id

        status = self.status.value

        computed_at = self.computed_at.isoformat()

        score: float | None | Unset
        if isinstance(self.score, Unset):
            score = UNSET
        else:
            score = self.score

        sub_scores: dict[str, Any] | Unset = UNSET
        if not isinstance(self.sub_scores, Unset):
            sub_scores = self.sub_scores.to_dict()

        pass_ = self.pass_

        raw: dict[str, Any] | Unset = UNSET
        if not isinstance(self.raw, Unset):
            raw = self.raw.to_dict()

        artifacts_uri = self.artifacts_uri

        trace_id = self.trace_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "task_id": task_id,
                "status": status,
                "computed_at": computed_at,
            }
        )
        if score is not UNSET:
            field_dict["score"] = score
        if sub_scores is not UNSET:
            field_dict["sub_scores"] = sub_scores
        if pass_ is not UNSET:
            field_dict["pass"] = pass_
        if raw is not UNSET:
            field_dict["raw"] = raw
        if artifacts_uri is not UNSET:
            field_dict["artifacts_uri"] = artifacts_uri
        if trace_id is not UNSET:
            field_dict["trace_id"] = trace_id

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.task_result_raw import TaskResultRaw
        from ..models.task_result_sub_scores import TaskResultSubScores

        d = dict(src_dict)
        task_id = d.pop("task_id")

        status = ResultStatus(d.pop("status"))

        computed_at = isoparse(d.pop("computed_at"))

        def _parse_score(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        score = _parse_score(d.pop("score", UNSET))

        _sub_scores = d.pop("sub_scores", UNSET)
        sub_scores: TaskResultSubScores | Unset
        if isinstance(_sub_scores, Unset):
            sub_scores = UNSET
        else:
            sub_scores = TaskResultSubScores.from_dict(_sub_scores)

        pass_ = d.pop("pass", UNSET)

        _raw = d.pop("raw", UNSET)
        raw: TaskResultRaw | Unset
        if isinstance(_raw, Unset):
            raw = UNSET
        else:
            raw = TaskResultRaw.from_dict(_raw)

        artifacts_uri = d.pop("artifacts_uri", UNSET)

        trace_id = d.pop("trace_id", UNSET)

        task_result = cls(
            task_id=task_id,
            status=status,
            computed_at=computed_at,
            score=score,
            sub_scores=sub_scores,
            pass_=pass_,
            raw=raw,
            artifacts_uri=artifacts_uri,
            trace_id=trace_id,
        )

        task_result.additional_properties = d
        return task_result

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
