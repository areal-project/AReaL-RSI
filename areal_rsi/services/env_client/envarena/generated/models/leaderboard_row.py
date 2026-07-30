from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.job_status import JobStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_spec import ModelSpec
    from ..models.version_ref import VersionRef


T = TypeVar("T", bound="LeaderboardRow")


@_attrs_define
class LeaderboardRow:
    """
    Attributes:
        rank (int):
        job_id (str):
        sample_numbers (int):
        task_count (int):
        ok_count (int):
        failed_count (int):
        status (JobStatus):
        created_at (datetime.datetime):
        computed_at (datetime.datetime):
        harness_key (str | Unset):
        benchmark_ref (VersionRef | Unset):
        harness_ref (VersionRef | Unset):
        model_ref (VersionRef | Unset):
        model (ModelSpec | Unset): Compact model runtime config. EnvArena recognizes model_name, parameters, and env;
            unknown fields are preserved but are not used for routing.
        aggregate_score (float | None | Unset):
    """

    rank: int
    job_id: str
    sample_numbers: int
    task_count: int
    ok_count: int
    failed_count: int
    status: JobStatus
    created_at: datetime.datetime
    computed_at: datetime.datetime
    harness_key: str | Unset = UNSET
    benchmark_ref: VersionRef | Unset = UNSET
    harness_ref: VersionRef | Unset = UNSET
    model_ref: VersionRef | Unset = UNSET
    model: ModelSpec | Unset = UNSET
    aggregate_score: float | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        rank = self.rank

        job_id = self.job_id

        sample_numbers = self.sample_numbers

        task_count = self.task_count

        ok_count = self.ok_count

        failed_count = self.failed_count

        status = self.status.value

        created_at = self.created_at.isoformat()

        computed_at = self.computed_at.isoformat()

        harness_key = self.harness_key

        benchmark_ref: dict[str, Any] | Unset = UNSET
        if not isinstance(self.benchmark_ref, Unset):
            benchmark_ref = self.benchmark_ref.to_dict()

        harness_ref: dict[str, Any] | Unset = UNSET
        if not isinstance(self.harness_ref, Unset):
            harness_ref = self.harness_ref.to_dict()

        model_ref: dict[str, Any] | Unset = UNSET
        if not isinstance(self.model_ref, Unset):
            model_ref = self.model_ref.to_dict()

        model: dict[str, Any] | Unset = UNSET
        if not isinstance(self.model, Unset):
            model = self.model.to_dict()

        aggregate_score: float | None | Unset
        if isinstance(self.aggregate_score, Unset):
            aggregate_score = UNSET
        else:
            aggregate_score = self.aggregate_score

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "rank": rank,
                "job_id": job_id,
                "sample_numbers": sample_numbers,
                "task_count": task_count,
                "ok_count": ok_count,
                "failed_count": failed_count,
                "status": status,
                "created_at": created_at,
                "computed_at": computed_at,
            }
        )
        if harness_key is not UNSET:
            field_dict["harness_key"] = harness_key
        if benchmark_ref is not UNSET:
            field_dict["benchmark_ref"] = benchmark_ref
        if harness_ref is not UNSET:
            field_dict["harness_ref"] = harness_ref
        if model_ref is not UNSET:
            field_dict["model_ref"] = model_ref
        if model is not UNSET:
            field_dict["model"] = model
        if aggregate_score is not UNSET:
            field_dict["aggregate_score"] = aggregate_score

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.model_spec import ModelSpec
        from ..models.version_ref import VersionRef

        d = dict(src_dict)
        rank = d.pop("rank")

        job_id = d.pop("job_id")

        sample_numbers = d.pop("sample_numbers")

        task_count = d.pop("task_count")

        ok_count = d.pop("ok_count")

        failed_count = d.pop("failed_count")

        status = JobStatus(d.pop("status"))

        created_at = isoparse(d.pop("created_at"))

        computed_at = isoparse(d.pop("computed_at"))

        harness_key = d.pop("harness_key", UNSET)

        _benchmark_ref = d.pop("benchmark_ref", UNSET)
        benchmark_ref: VersionRef | Unset
        if isinstance(_benchmark_ref, Unset):
            benchmark_ref = UNSET
        else:
            benchmark_ref = VersionRef.from_dict(_benchmark_ref)

        _harness_ref = d.pop("harness_ref", UNSET)
        harness_ref: VersionRef | Unset
        if isinstance(_harness_ref, Unset):
            harness_ref = UNSET
        else:
            harness_ref = VersionRef.from_dict(_harness_ref)

        _model_ref = d.pop("model_ref", UNSET)
        model_ref: VersionRef | Unset
        if isinstance(_model_ref, Unset):
            model_ref = UNSET
        else:
            model_ref = VersionRef.from_dict(_model_ref)

        _model = d.pop("model", UNSET)
        model: ModelSpec | Unset
        if isinstance(_model, Unset):
            model = UNSET
        else:
            model = ModelSpec.from_dict(_model)

        def _parse_aggregate_score(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        aggregate_score = _parse_aggregate_score(d.pop("aggregate_score", UNSET))

        leaderboard_row = cls(
            rank=rank,
            job_id=job_id,
            sample_numbers=sample_numbers,
            task_count=task_count,
            ok_count=ok_count,
            failed_count=failed_count,
            status=status,
            created_at=created_at,
            computed_at=computed_at,
            harness_key=harness_key,
            benchmark_ref=benchmark_ref,
            harness_ref=harness_ref,
            model_ref=model_ref,
            model=model,
            aggregate_score=aggregate_score,
        )

        leaderboard_row.additional_properties = d
        return leaderboard_row

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
