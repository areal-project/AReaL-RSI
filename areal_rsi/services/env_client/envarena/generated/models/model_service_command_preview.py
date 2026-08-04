from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="ModelServiceCommandPreview")


@_attrs_define
class ModelServiceCommandPreview:
    """
    Attributes:
        object_ (str):  Example: sglang_command_preview.
        launcher_module (str):  Example: sglang serve.
        argv (list[str]):
        command (str):
    """

    object_: str
    launcher_module: str
    argv: list[str]
    command: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        object_ = self.object_

        launcher_module = self.launcher_module

        argv = self.argv

        command = self.command

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "object": object_,
                "launcher_module": launcher_module,
                "argv": argv,
                "command": command,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        object_ = d.pop("object")

        launcher_module = d.pop("launcher_module")

        argv = cast(list[str], d.pop("argv"))

        command = d.pop("command")

        model_service_command_preview = cls(
            object_=object_,
            launcher_module=launcher_module,
            argv=argv,
            command=command,
        )

        model_service_command_preview.additional_properties = d
        return model_service_command_preview

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
