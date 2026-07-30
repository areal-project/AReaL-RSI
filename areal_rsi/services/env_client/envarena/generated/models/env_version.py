from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.entity_status import EntityStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.env_spec import EnvSpec


T = TypeVar("T", bound="EnvVersion")


@_attrs_define
class EnvVersion:
    """
    Attributes:
        key (str):
        version (str):
        status (EntityStatus):
        created_at (datetime.datetime):
        spec (EnvSpec): Env can be files-backed or image-backed. The agent workspace is
            ARENA_WORKSPACE=/envarena/workspace; OSS-backed platform mounts are namespaced under /envarena/ (query, files,
            out, result, oracle).
        content_hash (str | Unset):
        supersedes (str | Unset):
        created_by (str | Unset):
        published_at (datetime.datetime | Unset):
        published_by (str | Unset):
    """

    key: str
    version: str
    status: EntityStatus
    created_at: datetime.datetime
    spec: EnvSpec
    content_hash: str | Unset = UNSET
    supersedes: str | Unset = UNSET
    created_by: str | Unset = UNSET
    published_at: datetime.datetime | Unset = UNSET
    published_by: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        key = self.key

        version = self.version

        status = self.status.value

        created_at = self.created_at.isoformat()

        spec = self.spec.to_dict()

        content_hash = self.content_hash

        supersedes = self.supersedes

        created_by = self.created_by

        published_at: str | Unset = UNSET
        if not isinstance(self.published_at, Unset):
            published_at = self.published_at.isoformat()

        published_by = self.published_by

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "key": key,
                "version": version,
                "status": status,
                "created_at": created_at,
                "spec": spec,
            }
        )
        if content_hash is not UNSET:
            field_dict["content_hash"] = content_hash
        if supersedes is not UNSET:
            field_dict["supersedes"] = supersedes
        if created_by is not UNSET:
            field_dict["created_by"] = created_by
        if published_at is not UNSET:
            field_dict["published_at"] = published_at
        if published_by is not UNSET:
            field_dict["published_by"] = published_by

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.env_spec import EnvSpec

        d = dict(src_dict)
        key = d.pop("key")

        version = d.pop("version")

        status = EntityStatus(d.pop("status"))

        created_at = isoparse(d.pop("created_at"))

        spec = EnvSpec.from_dict(d.pop("spec"))

        content_hash = d.pop("content_hash", UNSET)

        supersedes = d.pop("supersedes", UNSET)

        created_by = d.pop("created_by", UNSET)

        _published_at = d.pop("published_at", UNSET)
        published_at: datetime.datetime | Unset
        if isinstance(_published_at, Unset):
            published_at = UNSET
        else:
            published_at = isoparse(_published_at)

        published_by = d.pop("published_by", UNSET)

        env_version = cls(
            key=key,
            version=version,
            status=status,
            created_at=created_at,
            spec=spec,
            content_hash=content_hash,
            supersedes=supersedes,
            created_by=created_by,
            published_at=published_at,
            published_by=published_by,
        )

        env_version.additional_properties = d
        return env_version

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
