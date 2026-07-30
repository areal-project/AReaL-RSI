from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="RewardReviewEvidenceAsset")


@_attrs_define
class RewardReviewEvidenceAsset:
    """
    Attributes:
        name (str):
        mime_type (str):
        uri (str): Authenticated, opaque endpoint for the declared evidence file.
    """

    name: str
    mime_type: str
    uri: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        mime_type = self.mime_type

        uri = self.uri

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
                "mime_type": mime_type,
                "uri": uri,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        name = d.pop("name")

        mime_type = d.pop("mime_type")

        uri = d.pop("uri")

        reward_review_evidence_asset = cls(
            name=name,
            mime_type=mime_type,
            uri=uri,
        )

        reward_review_evidence_asset.additional_properties = d
        return reward_review_evidence_asset

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
