from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

T = TypeVar("T", bound="TaskTraceSpanQuery")


@_attrs_define
class TaskTraceSpanQuery:
    """
    Attributes:
        backend (str | Unset):
        ok (bool | Unset):
        status (int | Unset):
        error (str | Unset):
        queried_at (datetime.datetime | Unset):
    """

    backend: str | Unset = UNSET
    ok: bool | Unset = UNSET
    status: int | Unset = UNSET
    error: str | Unset = UNSET
    queried_at: datetime.datetime | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        backend = self.backend

        ok = self.ok

        status = self.status

        error = self.error

        queried_at: str | Unset = UNSET
        if not isinstance(self.queried_at, Unset):
            queried_at = self.queried_at.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if backend is not UNSET:
            field_dict["backend"] = backend
        if ok is not UNSET:
            field_dict["ok"] = ok
        if status is not UNSET:
            field_dict["status"] = status
        if error is not UNSET:
            field_dict["error"] = error
        if queried_at is not UNSET:
            field_dict["queried_at"] = queried_at

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        backend = d.pop("backend", UNSET)

        ok = d.pop("ok", UNSET)

        status = d.pop("status", UNSET)

        error = d.pop("error", UNSET)

        _queried_at = d.pop("queried_at", UNSET)
        queried_at: datetime.datetime | Unset
        if isinstance(_queried_at, Unset):
            queried_at = UNSET
        else:
            queried_at = isoparse(_queried_at)

        task_trace_span_query = cls(
            backend=backend,
            ok=ok,
            status=status,
            error=error,
            queried_at=queried_at,
        )

        task_trace_span_query.additional_properties = d
        return task_trace_span_query

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
