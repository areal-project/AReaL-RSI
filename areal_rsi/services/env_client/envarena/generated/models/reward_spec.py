from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.eval_model_spec import EvalModelSpec
    from ..models.io_contract import IOContract
    from ..models.resource_spec import ResourceSpec
    from ..models.reward_spec_metadata import RewardSpecMetadata
    from ..models.runtime_image import RuntimeImage
    from ..models.score_schema import ScoreSchema


T = TypeVar("T", bound="RewardSpec")


@_attrs_define
class RewardSpec:
    """
    Attributes:
        runtime_image (RuntimeImage):
        entrypoint (str):
        score_schema (ScoreSchema):
        ttl_seconds (int):
        io_contract (IOContract | Unset):
        accepted_oracle_schema (str | Unset):
        eval_model (EvalModelSpec | Unset):
        resource_spec (ResourceSpec | Unset):
        metadata (RewardSpecMetadata | Unset):
    """

    runtime_image: RuntimeImage
    entrypoint: str
    score_schema: ScoreSchema
    ttl_seconds: int
    io_contract: IOContract | Unset = UNSET
    accepted_oracle_schema: str | Unset = UNSET
    eval_model: EvalModelSpec | Unset = UNSET
    resource_spec: ResourceSpec | Unset = UNSET
    metadata: RewardSpecMetadata | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        runtime_image = self.runtime_image.to_dict()

        entrypoint = self.entrypoint

        score_schema = self.score_schema.to_dict()

        ttl_seconds = self.ttl_seconds

        io_contract: dict[str, Any] | Unset = UNSET
        if not isinstance(self.io_contract, Unset):
            io_contract = self.io_contract.to_dict()

        accepted_oracle_schema = self.accepted_oracle_schema

        eval_model: dict[str, Any] | Unset = UNSET
        if not isinstance(self.eval_model, Unset):
            eval_model = self.eval_model.to_dict()

        resource_spec: dict[str, Any] | Unset = UNSET
        if not isinstance(self.resource_spec, Unset):
            resource_spec = self.resource_spec.to_dict()

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "runtime_image": runtime_image,
                "entrypoint": entrypoint,
                "score_schema": score_schema,
                "ttl_seconds": ttl_seconds,
            }
        )
        if io_contract is not UNSET:
            field_dict["io_contract"] = io_contract
        if accepted_oracle_schema is not UNSET:
            field_dict["accepted_oracle_schema"] = accepted_oracle_schema
        if eval_model is not UNSET:
            field_dict["eval_model"] = eval_model
        if resource_spec is not UNSET:
            field_dict["resource_spec"] = resource_spec
        if metadata is not UNSET:
            field_dict["metadata"] = metadata

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.eval_model_spec import EvalModelSpec
        from ..models.io_contract import IOContract
        from ..models.resource_spec import ResourceSpec
        from ..models.reward_spec_metadata import RewardSpecMetadata
        from ..models.runtime_image import RuntimeImage
        from ..models.score_schema import ScoreSchema

        d = dict(src_dict)
        runtime_image = RuntimeImage.from_dict(d.pop("runtime_image"))

        entrypoint = d.pop("entrypoint")

        score_schema = ScoreSchema.from_dict(d.pop("score_schema"))

        ttl_seconds = d.pop("ttl_seconds")

        _io_contract = d.pop("io_contract", UNSET)
        io_contract: IOContract | Unset
        if isinstance(_io_contract, Unset):
            io_contract = UNSET
        else:
            io_contract = IOContract.from_dict(_io_contract)

        accepted_oracle_schema = d.pop("accepted_oracle_schema", UNSET)

        _eval_model = d.pop("eval_model", UNSET)
        eval_model: EvalModelSpec | Unset
        if isinstance(_eval_model, Unset):
            eval_model = UNSET
        else:
            eval_model = EvalModelSpec.from_dict(_eval_model)

        _resource_spec = d.pop("resource_spec", UNSET)
        resource_spec: ResourceSpec | Unset
        if isinstance(_resource_spec, Unset):
            resource_spec = UNSET
        else:
            resource_spec = ResourceSpec.from_dict(_resource_spec)

        _metadata = d.pop("metadata", UNSET)
        metadata: RewardSpecMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = RewardSpecMetadata.from_dict(_metadata)

        reward_spec = cls(
            runtime_image=runtime_image,
            entrypoint=entrypoint,
            score_schema=score_schema,
            ttl_seconds=ttl_seconds,
            io_contract=io_contract,
            accepted_oracle_schema=accepted_oracle_schema,
            eval_model=eval_model,
            resource_spec=resource_spec,
            metadata=metadata,
        )

        reward_spec.additional_properties = d
        return reward_spec

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
