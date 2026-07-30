from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.benchmark_env_ref_params import BenchmarkEnvRefParams
    from ..models.version_ref import VersionRef


T = TypeVar("T", bound="BenchmarkEnvRef")


@_attrs_define
class BenchmarkEnvRef:
    """
    Attributes:
        env_ref (VersionRef):
        weight (float | Unset):  Default: 1.0.
        params (BenchmarkEnvRefParams | Unset):
    """

    env_ref: VersionRef
    weight: float | Unset = 1.0
    params: BenchmarkEnvRefParams | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        env_ref = self.env_ref.to_dict()

        weight = self.weight

        params: dict[str, Any] | Unset = UNSET
        if not isinstance(self.params, Unset):
            params = self.params.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "env_ref": env_ref,
            }
        )
        if weight is not UNSET:
            field_dict["weight"] = weight
        if params is not UNSET:
            field_dict["params"] = params

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.benchmark_env_ref_params import BenchmarkEnvRefParams
        from ..models.version_ref import VersionRef

        d = dict(src_dict)
        env_ref = VersionRef.from_dict(d.pop("env_ref"))

        weight = d.pop("weight", UNSET)

        _params = d.pop("params", UNSET)
        params: BenchmarkEnvRefParams | Unset
        if isinstance(_params, Unset):
            params = UNSET
        else:
            params = BenchmarkEnvRefParams.from_dict(_params)

        benchmark_env_ref = cls(
            env_ref=env_ref,
            weight=weight,
            params=params,
        )

        benchmark_env_ref.additional_properties = d
        return benchmark_env_ref

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
