from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.llm_endpoint_health_state import LLMEndpointHealthState
from ..types import UNSET, Unset

T = TypeVar("T", bound="LLMEndpointHealth")


@_attrs_define
class LLMEndpointHealth:
    """
    Attributes:
        state (LLMEndpointHealthState): Runtime state as observed by the serving gateway instance.
        last_checked_at (datetime.datetime | Unset):
        last_healthy_at (datetime.datetime | Unset):
        last_error (str | Unset):
    """

    state: LLMEndpointHealthState
    last_checked_at: datetime.datetime | Unset = UNSET
    last_healthy_at: datetime.datetime | Unset = UNSET
    last_error: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        state = self.state.value

        last_checked_at: str | Unset = UNSET
        if not isinstance(self.last_checked_at, Unset):
            last_checked_at = self.last_checked_at.isoformat()

        last_healthy_at: str | Unset = UNSET
        if not isinstance(self.last_healthy_at, Unset):
            last_healthy_at = self.last_healthy_at.isoformat()

        last_error = self.last_error

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "state": state,
            }
        )
        if last_checked_at is not UNSET:
            field_dict["last_checked_at"] = last_checked_at
        if last_healthy_at is not UNSET:
            field_dict["last_healthy_at"] = last_healthy_at
        if last_error is not UNSET:
            field_dict["last_error"] = last_error

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        state = LLMEndpointHealthState(d.pop("state"))

        _last_checked_at = d.pop("last_checked_at", UNSET)
        last_checked_at: datetime.datetime | Unset
        if isinstance(_last_checked_at, Unset):
            last_checked_at = UNSET
        else:
            last_checked_at = isoparse(_last_checked_at)

        _last_healthy_at = d.pop("last_healthy_at", UNSET)
        last_healthy_at: datetime.datetime | Unset
        if isinstance(_last_healthy_at, Unset):
            last_healthy_at = UNSET
        else:
            last_healthy_at = isoparse(_last_healthy_at)

        last_error = d.pop("last_error", UNSET)

        llm_endpoint_health = cls(
            state=state,
            last_checked_at=last_checked_at,
            last_healthy_at=last_healthy_at,
            last_error=last_error,
        )

        llm_endpoint_health.additional_properties = d
        return llm_endpoint_health

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
