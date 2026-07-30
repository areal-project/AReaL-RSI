from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.cli_download import CLIDownload


T = TypeVar("T", bound="CLIManifest")


@_attrs_define
class CLIManifest:
    """
    Attributes:
        version (str):  Example: 1.2.0.
        downloads (list[CLIDownload]):
        notes (str | Unset):
        published_at (datetime.datetime | Unset):
        published_by (str | Unset):
    """

    version: str
    downloads: list[CLIDownload]
    notes: str | Unset = UNSET
    published_at: datetime.datetime | Unset = UNSET
    published_by: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        version = self.version

        downloads = []
        for downloads_item_data in self.downloads:
            downloads_item = downloads_item_data.to_dict()
            downloads.append(downloads_item)

        notes = self.notes

        published_at: str | Unset = UNSET
        if not isinstance(self.published_at, Unset):
            published_at = self.published_at.isoformat()

        published_by = self.published_by

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "version": version,
                "downloads": downloads,
            }
        )
        if notes is not UNSET:
            field_dict["notes"] = notes
        if published_at is not UNSET:
            field_dict["published_at"] = published_at
        if published_by is not UNSET:
            field_dict["published_by"] = published_by

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.cli_download import CLIDownload

        d = dict(src_dict)
        version = d.pop("version")

        downloads = []
        _downloads = d.pop("downloads")
        for downloads_item_data in _downloads:
            downloads_item = CLIDownload.from_dict(downloads_item_data)

            downloads.append(downloads_item)

        notes = d.pop("notes", UNSET)

        _published_at = d.pop("published_at", UNSET)
        published_at: datetime.datetime | Unset
        if isinstance(_published_at, Unset):
            published_at = UNSET
        else:
            published_at = isoparse(_published_at)

        published_by = d.pop("published_by", UNSET)

        cli_manifest = cls(
            version=version,
            downloads=downloads,
            notes=notes,
            published_at=published_at,
            published_by=published_by,
        )

        cli_manifest.additional_properties = d
        return cli_manifest

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
