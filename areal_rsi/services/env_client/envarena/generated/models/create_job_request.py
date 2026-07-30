from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.concurrency_policy import ConcurrencyPolicy
    from ..models.create_job_request_metadata import CreateJobRequestMetadata
    from ..models.model_spec import ModelSpec
    from ..models.version_ref import VersionRef


T = TypeVar("T", bound="CreateJobRequest")


@_attrs_define
class CreateJobRequest:
    """
    Attributes:
        benchmark_ref (VersionRef):
        harness_ref (VersionRef):
        job_id (str | Unset): Optional readable Job short name, for example programbench. The server appends
            -YYMMDDHHMM; if omitted, it uses job-YYMMDDHHMM. A short numeric suffix resolves same-minute collisions.
            Example: programbench.
        model_ref (VersionRef | Unset):
        model (ModelSpec | Unset): Compact model runtime config. EnvArena recognizes model_name, parameters, and env;
            unknown fields are preserved but are not used for routing.
        sample_numbers (int | Unset): pass@k sample count. Each Env fans out into this many independent Tasks. Default:
            1.
        concurrency_policy (ConcurrencyPolicy | Unset):
        tags (list[str] | Unset): Selection and search labels attached to this Job.
        metadata (CreateJobRequestMetadata | Unset):
    """

    benchmark_ref: VersionRef
    harness_ref: VersionRef
    job_id: str | Unset = UNSET
    model_ref: VersionRef | Unset = UNSET
    model: ModelSpec | Unset = UNSET
    sample_numbers: int | Unset = 1
    concurrency_policy: ConcurrencyPolicy | Unset = UNSET
    tags: list[str] | Unset = UNSET
    metadata: CreateJobRequestMetadata | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        benchmark_ref = self.benchmark_ref.to_dict()

        harness_ref = self.harness_ref.to_dict()

        job_id = self.job_id

        model_ref: dict[str, Any] | Unset = UNSET
        if not isinstance(self.model_ref, Unset):
            model_ref = self.model_ref.to_dict()

        model: dict[str, Any] | Unset = UNSET
        if not isinstance(self.model, Unset):
            model = self.model.to_dict()

        sample_numbers = self.sample_numbers

        concurrency_policy: dict[str, Any] | Unset = UNSET
        if not isinstance(self.concurrency_policy, Unset):
            concurrency_policy = self.concurrency_policy.to_dict()

        tags: list[str] | Unset = UNSET
        if not isinstance(self.tags, Unset):
            tags = self.tags

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "benchmark_ref": benchmark_ref,
                "harness_ref": harness_ref,
            }
        )
        if job_id is not UNSET:
            field_dict["job_id"] = job_id
        if model_ref is not UNSET:
            field_dict["model_ref"] = model_ref
        if model is not UNSET:
            field_dict["model"] = model
        if sample_numbers is not UNSET:
            field_dict["sample_numbers"] = sample_numbers
        if concurrency_policy is not UNSET:
            field_dict["concurrency_policy"] = concurrency_policy
        if tags is not UNSET:
            field_dict["tags"] = tags
        if metadata is not UNSET:
            field_dict["metadata"] = metadata

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.concurrency_policy import ConcurrencyPolicy
        from ..models.create_job_request_metadata import CreateJobRequestMetadata
        from ..models.model_spec import ModelSpec
        from ..models.version_ref import VersionRef

        d = dict(src_dict)
        benchmark_ref = VersionRef.from_dict(d.pop("benchmark_ref"))

        harness_ref = VersionRef.from_dict(d.pop("harness_ref"))

        job_id = d.pop("job_id", UNSET)

        _model_ref = d.pop("model_ref", UNSET)
        model_ref: VersionRef | Unset
        if isinstance(_model_ref, Unset):
            model_ref = UNSET
        else:
            model_ref = VersionRef.from_dict(_model_ref)

        _model = d.pop("model", UNSET)
        model: ModelSpec | Unset
        if isinstance(_model, Unset):
            model = UNSET
        else:
            model = ModelSpec.from_dict(_model)

        sample_numbers = d.pop("sample_numbers", UNSET)

        _concurrency_policy = d.pop("concurrency_policy", UNSET)
        concurrency_policy: ConcurrencyPolicy | Unset
        if isinstance(_concurrency_policy, Unset):
            concurrency_policy = UNSET
        else:
            concurrency_policy = ConcurrencyPolicy.from_dict(_concurrency_policy)

        tags = cast(list[str], d.pop("tags", UNSET))

        _metadata = d.pop("metadata", UNSET)
        metadata: CreateJobRequestMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = CreateJobRequestMetadata.from_dict(_metadata)

        create_job_request = cls(
            benchmark_ref=benchmark_ref,
            harness_ref=harness_ref,
            job_id=job_id,
            model_ref=model_ref,
            model=model,
            sample_numbers=sample_numbers,
            concurrency_policy=concurrency_policy,
            tags=tags,
            metadata=metadata,
        )

        create_job_request.additional_properties = d
        return create_job_request

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
