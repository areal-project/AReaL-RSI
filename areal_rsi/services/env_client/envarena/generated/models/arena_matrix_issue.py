from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.arena_matrix_issue_status import ArenaMatrixIssueStatus

T = TypeVar("T", bound="ArenaMatrixIssue")


@_attrs_define
class ArenaMatrixIssue:
    """
    Attributes:
        id (str):
        title (str):
        job_ids (list[str]):
        status (ArenaMatrixIssueStatus):
    """

    id: str
    title: str
    job_ids: list[str]
    status: ArenaMatrixIssueStatus
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        title = self.title

        job_ids = self.job_ids

        status = self.status.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "title": title,
                "job_ids": job_ids,
                "status": status,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        id = d.pop("id")

        title = d.pop("title")

        job_ids = cast(list[str], d.pop("job_ids"))

        status = ArenaMatrixIssueStatus(d.pop("status"))

        arena_matrix_issue = cls(
            id=id,
            title=title,
            job_ids=job_ids,
            status=status,
        )

        arena_matrix_issue.additional_properties = d
        return arena_matrix_issue

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
