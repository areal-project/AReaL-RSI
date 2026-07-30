from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.concurrency_policy import ConcurrencyPolicy
    from ..models.create_rescore_job_request_metadata import (
        CreateRescoreJobRequestMetadata,
    )
    from ..models.version_ref import VersionRef


T = TypeVar("T", bound="CreateRescoreJobRequest")


@_attrs_define
class CreateRescoreJobRequest:
    """
    Attributes:
        reward_ref (VersionRef):
        job_id (str | Unset): Optional readable name for the derived Job. A unique -rescore suffix is generated when
            omitted.
        concurrency_policy (ConcurrencyPolicy | Unset):
        tags (list[str] | Unset): Selection and search labels attached to the derived Job.
        metadata (CreateRescoreJobRequestMetadata | Unset):
    """

    reward_ref: VersionRef
    job_id: str | Unset = UNSET
    concurrency_policy: ConcurrencyPolicy | Unset = UNSET
    tags: list[str] | Unset = UNSET
    metadata: CreateRescoreJobRequestMetadata | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        reward_ref = self.reward_ref.to_dict()

        job_id = self.job_id

        concurrency_policy: dict[str, Any] | Unset = UNSET
        if not isinstance(self.concurrency_policy, Unset):
            concurrency_policy = self.concurrency_policy.to_dict()

        tags: list[str] | Unset = UNSET
        if not isinstance(self.tags, Unset):
            tags = self.tags

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "reward_ref": reward_ref,
            }
        )
        if job_id is not UNSET:
            field_dict["job_id"] = job_id
        if concurrency_policy is not UNSET:
            field_dict["concurrency_policy"] = concurrency_policy
        if tags is not UNSET:
            field_dict["tags"] = tags
        if metadata is not UNSET:
            field_dict["metadata"] = metadata

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.concurrency_policy import ConcurrencyPolicy
        from ..models.create_rescore_job_request_metadata import (
            CreateRescoreJobRequestMetadata,
        )
        from ..models.version_ref import VersionRef

        d = dict(src_dict)
        reward_ref = VersionRef.from_dict(d.pop("reward_ref"))

        job_id = d.pop("job_id", UNSET)

        _concurrency_policy = d.pop("concurrency_policy", UNSET)
        concurrency_policy: ConcurrencyPolicy | Unset
        if isinstance(_concurrency_policy, Unset):
            concurrency_policy = UNSET
        else:
            concurrency_policy = ConcurrencyPolicy.from_dict(_concurrency_policy)

        tags = cast(list[str], d.pop("tags", UNSET))

        _metadata = d.pop("metadata", UNSET)
        metadata: CreateRescoreJobRequestMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = CreateRescoreJobRequestMetadata.from_dict(_metadata)

        create_rescore_job_request = cls(
            reward_ref=reward_ref,
            job_id=job_id,
            concurrency_policy=concurrency_policy,
            tags=tags,
            metadata=metadata,
        )

        create_rescore_job_request.additional_properties = d
        return create_rescore_job_request

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
