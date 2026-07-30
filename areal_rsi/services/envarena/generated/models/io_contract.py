from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.io_contract_metadata import IOContractMetadata


T = TypeVar("T", bound="IOContract")


@_attrs_define
class IOContract:
    """
    Attributes:
        entrypoint (str | Unset):
        required_env (list[str] | Unset):
        output_file (str | Unset):
        artifact_globs (list[str] | Unset):
        exit_code (str | Unset):  Default: 'zero_means_success'.
        metadata (IOContractMetadata | Unset):
    """

    entrypoint: str | Unset = UNSET
    required_env: list[str] | Unset = UNSET
    output_file: str | Unset = UNSET
    artifact_globs: list[str] | Unset = UNSET
    exit_code: str | Unset = "zero_means_success"
    metadata: IOContractMetadata | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        entrypoint = self.entrypoint

        required_env: list[str] | Unset = UNSET
        if not isinstance(self.required_env, Unset):
            required_env = self.required_env

        output_file = self.output_file

        artifact_globs: list[str] | Unset = UNSET
        if not isinstance(self.artifact_globs, Unset):
            artifact_globs = self.artifact_globs

        exit_code = self.exit_code

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if entrypoint is not UNSET:
            field_dict["entrypoint"] = entrypoint
        if required_env is not UNSET:
            field_dict["required_env"] = required_env
        if output_file is not UNSET:
            field_dict["output_file"] = output_file
        if artifact_globs is not UNSET:
            field_dict["artifact_globs"] = artifact_globs
        if exit_code is not UNSET:
            field_dict["exit_code"] = exit_code
        if metadata is not UNSET:
            field_dict["metadata"] = metadata

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.io_contract_metadata import IOContractMetadata

        d = dict(src_dict)
        entrypoint = d.pop("entrypoint", UNSET)

        required_env = cast(list[str], d.pop("required_env", UNSET))

        output_file = d.pop("output_file", UNSET)

        artifact_globs = cast(list[str], d.pop("artifact_globs", UNSET))

        exit_code = d.pop("exit_code", UNSET)

        _metadata = d.pop("metadata", UNSET)
        metadata: IOContractMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = IOContractMetadata.from_dict(_metadata)

        io_contract = cls(
            entrypoint=entrypoint,
            required_env=required_env,
            output_file=output_file,
            artifact_globs=artifact_globs,
            exit_code=exit_code,
            metadata=metadata,
        )

        io_contract.additional_properties = d
        return io_contract

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
