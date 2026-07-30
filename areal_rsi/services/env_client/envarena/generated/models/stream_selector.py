from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

T = TypeVar("T", bound="StreamSelector")


@_attrs_define
class StreamSelector:
    """
    Attributes:
        partitions (list[str] | Unset): Built-in Env time partitions such as 20260713 or 2026071308.
        include_tags (list[str] | Unset):
        exclude_tags (list[str] | Unset):
        created_after (datetime.datetime | Unset):
        created_before (datetime.datetime | Unset):
        limit (int | Unset):
    """

    partitions: list[str] | Unset = UNSET
    include_tags: list[str] | Unset = UNSET
    exclude_tags: list[str] | Unset = UNSET
    created_after: datetime.datetime | Unset = UNSET
    created_before: datetime.datetime | Unset = UNSET
    limit: int | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        partitions: list[str] | Unset = UNSET
        if not isinstance(self.partitions, Unset):
            partitions = self.partitions

        include_tags: list[str] | Unset = UNSET
        if not isinstance(self.include_tags, Unset):
            include_tags = self.include_tags

        exclude_tags: list[str] | Unset = UNSET
        if not isinstance(self.exclude_tags, Unset):
            exclude_tags = self.exclude_tags

        created_after: str | Unset = UNSET
        if not isinstance(self.created_after, Unset):
            created_after = self.created_after.isoformat()

        created_before: str | Unset = UNSET
        if not isinstance(self.created_before, Unset):
            created_before = self.created_before.isoformat()

        limit = self.limit

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if partitions is not UNSET:
            field_dict["partitions"] = partitions
        if include_tags is not UNSET:
            field_dict["include_tags"] = include_tags
        if exclude_tags is not UNSET:
            field_dict["exclude_tags"] = exclude_tags
        if created_after is not UNSET:
            field_dict["created_after"] = created_after
        if created_before is not UNSET:
            field_dict["created_before"] = created_before
        if limit is not UNSET:
            field_dict["limit"] = limit

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        partitions = cast(list[str], d.pop("partitions", UNSET))

        include_tags = cast(list[str], d.pop("include_tags", UNSET))

        exclude_tags = cast(list[str], d.pop("exclude_tags", UNSET))

        _created_after = d.pop("created_after", UNSET)
        created_after: datetime.datetime | Unset
        if isinstance(_created_after, Unset):
            created_after = UNSET
        else:
            created_after = isoparse(_created_after)

        _created_before = d.pop("created_before", UNSET)
        created_before: datetime.datetime | Unset
        if isinstance(_created_before, Unset):
            created_before = UNSET
        else:
            created_before = isoparse(_created_before)

        limit = d.pop("limit", UNSET)

        stream_selector = cls(
            partitions=partitions,
            include_tags=include_tags,
            exclude_tags=exclude_tags,
            created_after=created_after,
            created_before=created_before,
            limit=limit,
        )

        stream_selector.additional_properties = d
        return stream_selector

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
