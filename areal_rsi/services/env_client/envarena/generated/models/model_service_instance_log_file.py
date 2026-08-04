from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

T = TypeVar("T", bound="ModelServiceInstanceLogFile")


@_attrs_define
class ModelServiceInstanceLogFile:
    """
    Attributes:
        file_name (str):
        path (str):
        size_bytes (int):
        truncated (bool):
        start_offset (int):
        bytes_read (int):
        lines_returned (int):
        has_more_lines (bool):
        modified_at (datetime.datetime | Unset):
        content (str | Unset):
        line_offset (int | Unset):
        line_limit (int | Unset):
        next_line_offset (int | Unset):
    """

    file_name: str
    path: str
    size_bytes: int
    truncated: bool
    start_offset: int
    bytes_read: int
    lines_returned: int
    has_more_lines: bool
    modified_at: datetime.datetime | Unset = UNSET
    content: str | Unset = UNSET
    line_offset: int | Unset = UNSET
    line_limit: int | Unset = UNSET
    next_line_offset: int | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        file_name = self.file_name

        path = self.path

        size_bytes = self.size_bytes

        truncated = self.truncated

        start_offset = self.start_offset

        bytes_read = self.bytes_read

        lines_returned = self.lines_returned

        has_more_lines = self.has_more_lines

        modified_at: str | Unset = UNSET
        if not isinstance(self.modified_at, Unset):
            modified_at = self.modified_at.isoformat()

        content = self.content

        line_offset = self.line_offset

        line_limit = self.line_limit

        next_line_offset = self.next_line_offset

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "file_name": file_name,
                "path": path,
                "size_bytes": size_bytes,
                "truncated": truncated,
                "start_offset": start_offset,
                "bytes_read": bytes_read,
                "lines_returned": lines_returned,
                "has_more_lines": has_more_lines,
            }
        )
        if modified_at is not UNSET:
            field_dict["modified_at"] = modified_at
        if content is not UNSET:
            field_dict["content"] = content
        if line_offset is not UNSET:
            field_dict["line_offset"] = line_offset
        if line_limit is not UNSET:
            field_dict["line_limit"] = line_limit
        if next_line_offset is not UNSET:
            field_dict["next_line_offset"] = next_line_offset

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        file_name = d.pop("file_name")

        path = d.pop("path")

        size_bytes = d.pop("size_bytes")

        truncated = d.pop("truncated")

        start_offset = d.pop("start_offset")

        bytes_read = d.pop("bytes_read")

        lines_returned = d.pop("lines_returned")

        has_more_lines = d.pop("has_more_lines")

        _modified_at = d.pop("modified_at", UNSET)
        modified_at: datetime.datetime | Unset
        if isinstance(_modified_at, Unset):
            modified_at = UNSET
        else:
            modified_at = isoparse(_modified_at)

        content = d.pop("content", UNSET)

        line_offset = d.pop("line_offset", UNSET)

        line_limit = d.pop("line_limit", UNSET)

        next_line_offset = d.pop("next_line_offset", UNSET)

        model_service_instance_log_file = cls(
            file_name=file_name,
            path=path,
            size_bytes=size_bytes,
            truncated=truncated,
            start_offset=start_offset,
            bytes_read=bytes_read,
            lines_returned=lines_returned,
            has_more_lines=has_more_lines,
            modified_at=modified_at,
            content=content,
            line_offset=line_offset,
            line_limit=line_limit,
            next_line_offset=next_line_offset,
        )

        model_service_instance_log_file.additional_properties = d
        return model_service_instance_log_file

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
