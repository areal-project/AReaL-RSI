from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.version_ref import VersionRef


T = TypeVar("T", bound="StreamDataRef")


@_attrs_define
class StreamDataRef:
    """
    Attributes:
        data_id (str):  Example: env:tau3-retail-001@0.1.0.
        env_ref (VersionRef):
        partition (str | Unset):
        tags (list[str] | Unset):
        created_at (datetime.datetime | Unset):
    """

    data_id: str
    env_ref: VersionRef
    partition: str | Unset = UNSET
    tags: list[str] | Unset = UNSET
    created_at: datetime.datetime | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data_id = self.data_id

        env_ref = self.env_ref.to_dict()

        partition = self.partition

        tags: list[str] | Unset = UNSET
        if not isinstance(self.tags, Unset):
            tags = self.tags

        created_at: str | Unset = UNSET
        if not isinstance(self.created_at, Unset):
            created_at = self.created_at.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "data_id": data_id,
                "env_ref": env_ref,
            }
        )
        if partition is not UNSET:
            field_dict["partition"] = partition
        if tags is not UNSET:
            field_dict["tags"] = tags
        if created_at is not UNSET:
            field_dict["created_at"] = created_at

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.version_ref import VersionRef

        d = dict(src_dict)
        data_id = d.pop("data_id")

        env_ref = VersionRef.from_dict(d.pop("env_ref"))

        partition = d.pop("partition", UNSET)

        tags = cast(list[str], d.pop("tags", UNSET))

        _created_at = d.pop("created_at", UNSET)
        created_at: datetime.datetime | Unset
        if isinstance(_created_at, Unset):
            created_at = UNSET
        else:
            created_at = isoparse(_created_at)

        stream_data_ref = cls(
            data_id=data_id,
            env_ref=env_ref,
            partition=partition,
            tags=tags,
            created_at=created_at,
        )

        stream_data_ref.additional_properties = d
        return stream_data_ref

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
