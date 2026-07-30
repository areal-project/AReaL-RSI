from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.aggregation_spec import AggregationSpec
    from ..models.job_result_leaderboard_key import JobResultLeaderboardKey
    from ..models.job_result_per_env_item import JobResultPerEnvItem


T = TypeVar("T", bound="JobResult")


@_attrs_define
class JobResult:
    """
    Attributes:
        job_id (str):
        aggregation (AggregationSpec):
        task_count (int):
        computed_at (datetime.datetime):
        aggregate_score (float | None | Unset):
        ok_count (int | Unset):
        failed_count (int | Unset):
        leaderboard_key (JobResultLeaderboardKey | Unset):
        per_env (list[JobResultPerEnvItem] | Unset):
    """

    job_id: str
    aggregation: AggregationSpec
    task_count: int
    computed_at: datetime.datetime
    aggregate_score: float | None | Unset = UNSET
    ok_count: int | Unset = UNSET
    failed_count: int | Unset = UNSET
    leaderboard_key: JobResultLeaderboardKey | Unset = UNSET
    per_env: list[JobResultPerEnvItem] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        job_id = self.job_id

        aggregation = self.aggregation.to_dict()

        task_count = self.task_count

        computed_at = self.computed_at.isoformat()

        aggregate_score: float | None | Unset
        if isinstance(self.aggregate_score, Unset):
            aggregate_score = UNSET
        else:
            aggregate_score = self.aggregate_score

        ok_count = self.ok_count

        failed_count = self.failed_count

        leaderboard_key: dict[str, Any] | Unset = UNSET
        if not isinstance(self.leaderboard_key, Unset):
            leaderboard_key = self.leaderboard_key.to_dict()

        per_env: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.per_env, Unset):
            per_env = []
            for per_env_item_data in self.per_env:
                per_env_item = per_env_item_data.to_dict()
                per_env.append(per_env_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "job_id": job_id,
                "aggregation": aggregation,
                "task_count": task_count,
                "computed_at": computed_at,
            }
        )
        if aggregate_score is not UNSET:
            field_dict["aggregate_score"] = aggregate_score
        if ok_count is not UNSET:
            field_dict["ok_count"] = ok_count
        if failed_count is not UNSET:
            field_dict["failed_count"] = failed_count
        if leaderboard_key is not UNSET:
            field_dict["leaderboard_key"] = leaderboard_key
        if per_env is not UNSET:
            field_dict["per_env"] = per_env

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.aggregation_spec import AggregationSpec
        from ..models.job_result_leaderboard_key import JobResultLeaderboardKey
        from ..models.job_result_per_env_item import JobResultPerEnvItem

        d = dict(src_dict)
        job_id = d.pop("job_id")

        aggregation = AggregationSpec.from_dict(d.pop("aggregation"))

        task_count = d.pop("task_count")

        computed_at = isoparse(d.pop("computed_at"))

        def _parse_aggregate_score(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        aggregate_score = _parse_aggregate_score(d.pop("aggregate_score", UNSET))

        ok_count = d.pop("ok_count", UNSET)

        failed_count = d.pop("failed_count", UNSET)

        _leaderboard_key = d.pop("leaderboard_key", UNSET)
        leaderboard_key: JobResultLeaderboardKey | Unset
        if isinstance(_leaderboard_key, Unset):
            leaderboard_key = UNSET
        else:
            leaderboard_key = JobResultLeaderboardKey.from_dict(_leaderboard_key)

        _per_env = d.pop("per_env", UNSET)
        per_env: list[JobResultPerEnvItem] | Unset = UNSET
        if _per_env is not UNSET:
            per_env = []
            for per_env_item_data in _per_env:
                per_env_item = JobResultPerEnvItem.from_dict(per_env_item_data)

                per_env.append(per_env_item)

        job_result = cls(
            job_id=job_id,
            aggregation=aggregation,
            task_count=task_count,
            computed_at=computed_at,
            aggregate_score=aggregate_score,
            ok_count=ok_count,
            failed_count=failed_count,
            leaderboard_key=leaderboard_key,
            per_env=per_env,
        )

        job_result.additional_properties = d
        return job_result

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
