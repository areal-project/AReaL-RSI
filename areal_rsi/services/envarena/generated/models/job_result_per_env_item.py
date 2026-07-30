from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.version_ref import VersionRef


T = TypeVar("T", bound="JobResultPerEnvItem")


@_attrs_define
class JobResultPerEnvItem:
    """
    Attributes:
        env_ref (VersionRef | Unset):
        score (float | None | Unset):
        task_ids (list[str] | Unset):
    """

    env_ref: VersionRef | Unset = UNSET
    score: float | None | Unset = UNSET
    task_ids: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        env_ref: dict[str, Any] | Unset = UNSET
        if not isinstance(self.env_ref, Unset):
            env_ref = self.env_ref.to_dict()

        score: float | None | Unset
        if isinstance(self.score, Unset):
            score = UNSET
        else:
            score = self.score

        task_ids: list[str] | Unset = UNSET
        if not isinstance(self.task_ids, Unset):
            task_ids = self.task_ids

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if env_ref is not UNSET:
            field_dict["env_ref"] = env_ref
        if score is not UNSET:
            field_dict["score"] = score
        if task_ids is not UNSET:
            field_dict["task_ids"] = task_ids

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.version_ref import VersionRef

        d = dict(src_dict)
        _env_ref = d.pop("env_ref", UNSET)
        env_ref: VersionRef | Unset
        if isinstance(_env_ref, Unset):
            env_ref = UNSET
        else:
            env_ref = VersionRef.from_dict(_env_ref)

        def _parse_score(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        score = _parse_score(d.pop("score", UNSET))

        task_ids = cast(list[str], d.pop("task_ids", UNSET))

        job_result_per_env_item = cls(
            env_ref=env_ref,
            score=score,
            task_ids=task_ids,
        )

        job_result_per_env_item.additional_properties = d
        return job_result_per_env_item

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
