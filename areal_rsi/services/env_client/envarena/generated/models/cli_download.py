from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="CLIDownload")


@_attrs_define
class CLIDownload:
    """
    Attributes:
        platform (str): GOOS-GOARCH token. Example: darwin-arm64.
        uri (str): uploads/ OSS URI (from POST /uploads) or an absolute http(s) URL. Example:
            uploads/260709/abc123/arena.
        sha256 (str | Unset): Optional hex SHA-256; when set, `arena update` verifies the download.
        size (int | Unset): Optional byte size.
        url (str | Unset): Derived at GET time — the streaming download route for this platform. Not persisted. Example:
            https://htnn-gateway.alipay.com/envarena/openapi/v1/cli/download/darwin-arm64.
    """

    platform: str
    uri: str
    sha256: str | Unset = UNSET
    size: int | Unset = UNSET
    url: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        platform = self.platform

        uri = self.uri

        sha256 = self.sha256

        size = self.size

        url = self.url

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "platform": platform,
                "uri": uri,
            }
        )
        if sha256 is not UNSET:
            field_dict["sha256"] = sha256
        if size is not UNSET:
            field_dict["size"] = size
        if url is not UNSET:
            field_dict["url"] = url

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        platform = d.pop("platform")

        uri = d.pop("uri")

        sha256 = d.pop("sha256", UNSET)

        size = d.pop("size", UNSET)

        url = d.pop("url", UNSET)

        cli_download = cls(
            platform=platform,
            uri=uri,
            sha256=sha256,
            size=size,
            url=url,
        )

        cli_download.additional_properties = d
        return cli_download

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
