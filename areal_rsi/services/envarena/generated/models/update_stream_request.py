from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.update_stream_request_status import UpdateStreamRequestStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.stream_selector import StreamSelector
    from ..models.update_stream_request_metadata import UpdateStreamRequestMetadata
    from ..models.version_ref import VersionRef


T = TypeVar("T", bound="UpdateStreamRequest")


@_attrs_define
class UpdateStreamRequest:
    """
    Attributes:
        name (str | Unset):
        description (str | Unset):
        task (str | Unset):
        selector (StreamSelector | Unset):
        default_harness_ref (VersionRef | Unset):
        default_reward_ref (VersionRef | Unset):
        status (UpdateStreamRequestStatus | Unset):
        metadata (UpdateStreamRequestMetadata | Unset):
    """

    name: str | Unset = UNSET
    description: str | Unset = UNSET
    task: str | Unset = UNSET
    selector: StreamSelector | Unset = UNSET
    default_harness_ref: VersionRef | Unset = UNSET
    default_reward_ref: VersionRef | Unset = UNSET
    status: UpdateStreamRequestStatus | Unset = UNSET
    metadata: UpdateStreamRequestMetadata | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        description = self.description

        task = self.task

        selector: dict[str, Any] | Unset = UNSET
        if not isinstance(self.selector, Unset):
            selector = self.selector.to_dict()

        default_harness_ref: dict[str, Any] | Unset = UNSET
        if not isinstance(self.default_harness_ref, Unset):
            default_harness_ref = self.default_harness_ref.to_dict()

        default_reward_ref: dict[str, Any] | Unset = UNSET
        if not isinstance(self.default_reward_ref, Unset):
            default_reward_ref = self.default_reward_ref.to_dict()

        status: str | Unset = UNSET
        if not isinstance(self.status, Unset):
            status = self.status.value

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if name is not UNSET:
            field_dict["name"] = name
        if description is not UNSET:
            field_dict["description"] = description
        if task is not UNSET:
            field_dict["task"] = task
        if selector is not UNSET:
            field_dict["selector"] = selector
        if default_harness_ref is not UNSET:
            field_dict["default_harness_ref"] = default_harness_ref
        if default_reward_ref is not UNSET:
            field_dict["default_reward_ref"] = default_reward_ref
        if status is not UNSET:
            field_dict["status"] = status
        if metadata is not UNSET:
            field_dict["metadata"] = metadata

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.stream_selector import StreamSelector
        from ..models.update_stream_request_metadata import UpdateStreamRequestMetadata
        from ..models.version_ref import VersionRef

        d = dict(src_dict)
        name = d.pop("name", UNSET)

        description = d.pop("description", UNSET)

        task = d.pop("task", UNSET)

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

        _status = d.pop("status", UNSET)
        status: UpdateStreamRequestStatus | Unset
        if isinstance(_status, Unset):
            status = UNSET
        else:
            status = UpdateStreamRequestStatus(_status)

        _metadata = d.pop("metadata", UNSET)
        metadata: UpdateStreamRequestMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = UpdateStreamRequestMetadata.from_dict(_metadata)

        update_stream_request = cls(
            name=name,
            description=description,
            task=task,
            selector=selector,
            default_harness_ref=default_harness_ref,
            default_reward_ref=default_reward_ref,
            status=status,
            metadata=metadata,
        )

        update_stream_request.additional_properties = d
        return update_stream_request

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
