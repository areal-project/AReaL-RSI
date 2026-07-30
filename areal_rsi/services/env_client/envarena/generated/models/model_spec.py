from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_spec_env import ModelSpecEnv
    from ..models.model_spec_parameters import ModelSpecParameters


T = TypeVar("T", bound="ModelSpec")


@_attrs_define
class ModelSpec:
    """Compact model runtime config. EnvArena recognizes model_name, parameters, and env; unknown fields are preserved but
    are not used for routing.

        Attributes:
            model_name (str):  Example: playforge_glm.
            parameters (ModelSpecParameters | Unset):
            env (ModelSpecEnv | Unset): Environment variables injected into Harness sandbox and Env image sandbox. Put model
                gateway and credentials here for now. ARENA_*, OTEL_*, TRACEPARENT/TRACESTATE, Claude Code telemetry controls,
                and IS_SANDBOX are reserved and cannot be overridden.
    """

    model_name: str
    parameters: ModelSpecParameters | Unset = UNSET
    env: ModelSpecEnv | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        model_name = self.model_name

        parameters: dict[str, Any] | Unset = UNSET
        if not isinstance(self.parameters, Unset):
            parameters = self.parameters.to_dict()

        env: dict[str, Any] | Unset = UNSET
        if not isinstance(self.env, Unset):
            env = self.env.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "model_name": model_name,
            }
        )
        if parameters is not UNSET:
            field_dict["parameters"] = parameters
        if env is not UNSET:
            field_dict["env"] = env

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.model_spec_env import ModelSpecEnv
        from ..models.model_spec_parameters import ModelSpecParameters

        d = dict(src_dict)
        model_name = d.pop("model_name")

        _parameters = d.pop("parameters", UNSET)
        parameters: ModelSpecParameters | Unset
        if isinstance(_parameters, Unset):
            parameters = UNSET
        else:
            parameters = ModelSpecParameters.from_dict(_parameters)

        _env = d.pop("env", UNSET)
        env: ModelSpecEnv | Unset
        if isinstance(_env, Unset):
            env = UNSET
        else:
            env = ModelSpecEnv.from_dict(_env)

        model_spec = cls(
            model_name=model_name,
            parameters=parameters,
            env=env,
        )

        model_spec.additional_properties = d
        return model_spec

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
