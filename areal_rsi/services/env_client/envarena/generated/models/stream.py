from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.stream_status import StreamStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.stream_metadata import StreamMetadata
    from ..models.stream_selector import StreamSelector
    from ..models.version_ref import VersionRef


T = TypeVar("T", bound="Stream")


@_attrs_define
class Stream:
    """
    Attributes:
        stream_id (str):
        name (str):
        task (str):
        status (StreamStatus):
        description (str | Unset):
        selector (StreamSelector | Unset):
        default_harness_ref (VersionRef | Unset):
        default_reward_ref (VersionRef | Unset):
        metadata (StreamMetadata | Unset):
        created_at (datetime.datetime | Unset):
        updated_at (datetime.datetime | Unset):
        created_by (str | Unset):
    """

    stream_id: str
    name: str
    task: str
    status: StreamStatus
    description: str | Unset = UNSET
    selector: StreamSelector | Unset = UNSET
    default_harness_ref: VersionRef | Unset = UNSET
    default_reward_ref: VersionRef | Unset = UNSET
    metadata: StreamMetadata | Unset = UNSET
    created_at: datetime.datetime | Unset = UNSET
    updated_at: datetime.datetime | Unset = UNSET
    created_by: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        stream_id = self.stream_id

        name = self.name

        task = self.task

        status = self.status.value

        description = self.description

        selector: dict[str, Any] | Unset = UNSET
        if not isinstance(self.selector, Unset):
            selector = self.selector.to_dict()

        default_harness_ref: dict[str, Any] | Unset = UNSET
        if not isinstance(self.default_harness_ref, Unset):
            default_harness_ref = self.default_harness_ref.to_dict()

        default_reward_ref: dict[str, Any] | Unset = UNSET
        if not isinstance(self.default_reward_ref, Unset):
            default_reward_ref = self.default_reward_ref.to_dict()

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

        created_at: str | Unset = UNSET
        if not isinstance(self.created_at, Unset):
            created_at = self.created_at.isoformat()

        updated_at: str | Unset = UNSET
        if not isinstance(self.updated_at, Unset):
            updated_at = self.updated_at.isoformat()

        created_by = self.created_by

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "stream_id": stream_id,
                "name": name,
                "task": task,
                "status": status,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description
        if selector is not UNSET:
            field_dict["selector"] = selector
        if default_harness_ref is not UNSET:
            field_dict["default_harness_ref"] = default_harness_ref
        if default_reward_ref is not UNSET:
            field_dict["default_reward_ref"] = default_reward_ref
        if metadata is not UNSET:
            field_dict["metadata"] = metadata
        if created_at is not UNSET:
            field_dict["created_at"] = created_at
        if updated_at is not UNSET:
            field_dict["updated_at"] = updated_at
        if created_by is not UNSET:
            field_dict["created_by"] = created_by

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.stream_metadata import StreamMetadata
        from ..models.stream_selector import StreamSelector
        from ..models.version_ref import VersionRef

        d = dict(src_dict)
        stream_id = d.pop("stream_id")

        name = d.pop("name")

        task = d.pop("task")

        status = StreamStatus(d.pop("status"))

        description = d.pop("description", UNSET)

        _selector = d.pop("selector", UNSET)
        selector: StreamSelector | Unset
        if isinstance(_selector, Unset):
            selector = UNSET
        else:
            selector = StreamSelector.from_dict(_selector)

        _default_harness_ref = d.pop("default_harness_ref", UNSET)
        default_harness_ref: VersionRef | Unset
        if isinstance(_default_harness_ref, Unset):
            default_harness_ref = UNSET
        else:
            default_harness_ref = VersionRef.from_dict(_default_harness_ref)

        _default_reward_ref = d.pop("default_reward_ref", UNSET)
        default_reward_ref: VersionRef | Unset
        if isinstance(_default_reward_ref, Unset):
            default_reward_ref = UNSET
        else:
            default_reward_ref = VersionRef.from_dict(_default_reward_ref)

        _metadata = d.pop("metadata", UNSET)
        metadata: StreamMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = StreamMetadata.from_dict(_metadata)

        _created_at = d.pop("created_at", UNSET)
        created_at: datetime.datetime | Unset
        if isinstance(_created_at, Unset):
            created_at = UNSET
        else:
            created_at = isoparse(_created_at)

        _updated_at = d.pop("updated_at", UNSET)
        updated_at: datetime.datetime | Unset
        if isinstance(_updated_at, Unset):
            updated_at = UNSET
        else:
            updated_at = isoparse(_updated_at)

        created_by = d.pop("created_by", UNSET)

        stream = cls(
            stream_id=stream_id,
            name=name,
            task=task,
            status=status,
            description=description,
            selector=selector,
            default_harness_ref=default_harness_ref,
            default_reward_ref=default_reward_ref,
            metadata=metadata,
            created_at=created_at,
            updated_at=updated_at,
            created_by=created_by,
        )

        stream.additional_properties = d
        return stream

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
