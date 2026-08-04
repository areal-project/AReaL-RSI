from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="PlayForgeFinishResponse")


@_attrs_define
class PlayForgeFinishResponse:
    """
    Attributes:
        task_id (str):
        status (str):
        finished (bool): False only while another EnvArena replica is destroying the PlayForge sandbox.
    """

    task_id: str
    status: str
    finished: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        task_id = self.task_id

        status = self.status

        finished = self.finished

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "task_id": task_id,
                "status": status,
                "finished": finished,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        task_id = d.pop("task_id")

        status = d.pop("status")

        finished = d.pop("finished")

        play_forge_finish_response = cls(
            task_id=task_id,
            status=status,
            finished=finished,
        )

        play_forge_finish_response.additional_properties = d
        return play_forge_finish_response

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
