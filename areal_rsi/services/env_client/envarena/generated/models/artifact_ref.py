from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.artifact_ref_role import ArtifactRefRole
from ..types import UNSET, Unset

T = TypeVar("T", bound="ArtifactRef")


@_attrs_define
class ArtifactRef:
    """
    Attributes:
        name (str):
        uri (str): Product artifact URL; screenshot URLs include the attempt query parameter.
        attempt (int | Unset): Task attempt number that produced the artifact.
        path (str | Unset): Runtime path originally reported by Harness or Reward, when available.
        type_ (str | Unset):
        role (ArtifactRefRole | Unset):
        size_bytes (int | Unset):
        digest (str | Unset):
    """

    name: str
    uri: str
    attempt: int | Unset = UNSET
    path: str | Unset = UNSET
    type_: str | Unset = UNSET
    role: ArtifactRefRole | Unset = UNSET
    size_bytes: int | Unset = UNSET
    digest: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        uri = self.uri

        attempt = self.attempt

        path = self.path

        type_ = self.type_

        role: str | Unset = UNSET
        if not isinstance(self.role, Unset):
            role = self.role.value

        size_bytes = self.size_bytes

        digest = self.digest

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
                "uri": uri,
            }
        )
        if attempt is not UNSET:
            field_dict["attempt"] = attempt
        if path is not UNSET:
            field_dict["path"] = path
        if type_ is not UNSET:
            field_dict["type"] = type_
        if role is not UNSET:
            field_dict["role"] = role
        if size_bytes is not UNSET:
            field_dict["size_bytes"] = size_bytes
        if digest is not UNSET:
            field_dict["digest"] = digest

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        name = d.pop("name")

        uri = d.pop("uri")

        attempt = d.pop("attempt", UNSET)

        path = d.pop("path", UNSET)

        type_ = d.pop("type", UNSET)

        _role = d.pop("role", UNSET)
        role: ArtifactRefRole | Unset
        if isinstance(_role, Unset):
            role = UNSET
        else:
            role = ArtifactRefRole(_role)

        size_bytes = d.pop("size_bytes", UNSET)

        digest = d.pop("digest", UNSET)

        artifact_ref = cls(
            name=name,
            uri=uri,
            attempt=attempt,
            path=path,
            type_=type_,
            role=role,
            size_bytes=size_bytes,
            digest=digest,
        )

        artifact_ref.additional_properties = d
        return artifact_ref

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
