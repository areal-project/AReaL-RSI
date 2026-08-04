from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.data_source import DataSource
    from ..models.env_runtime_spec import EnvRuntimeSpec
    from ..models.env_spec_metadata import EnvSpecMetadata
    from ..models.object_ref import ObjectRef
    from ..models.resource_spec import ResourceSpec


T = TypeVar("T", bound="EnvSpec")


@_attrs_define
class EnvSpec:
    """Env can be files-backed or image-backed. The agent workspace is ARENA_WORKSPACE=/envarena/workspace; OSS-backed
    platform mounts are namespaced under /envarena/ (query, files, out, result, oracle).

        Attributes:
            query (ObjectRef):
            runtime (EnvRuntimeSpec):
            timeout_seconds (int):  Default: 600.
            bundle (ObjectRef | Unset):
            oracle (ObjectRef | Unset):
            oracle_schema (str | Unset):  Example: feiji.oracle.v1.
            query_path (str | Unset): Defaults to /workspace/Target.md. Default: '/workspace/Target.md'.
            workdir (str | Unset): Absolute agent working directory for image Envs. Defaults to the image configured working
                directory. Example: /testbed.
            tags (list[str] | Unset):
            resource_hints (ResourceSpec | Unset):
            metadata (EnvSpecMetadata | Unset):
            data_source (DataSource | Unset): Object-store materialization override for Env bundles with non-standard
                layout.
    """

    query: ObjectRef
    runtime: EnvRuntimeSpec
    timeout_seconds: int = 600
    bundle: ObjectRef | Unset = UNSET
    oracle: ObjectRef | Unset = UNSET
    oracle_schema: str | Unset = UNSET
    query_path: str | Unset = "/workspace/Target.md"
    workdir: str | Unset = UNSET
    tags: list[str] | Unset = UNSET
    resource_hints: ResourceSpec | Unset = UNSET
    metadata: EnvSpecMetadata | Unset = UNSET
    data_source: DataSource | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        query = self.query.to_dict()

        runtime = self.runtime.to_dict()

        timeout_seconds = self.timeout_seconds

        bundle: dict[str, Any] | Unset = UNSET
        if not isinstance(self.bundle, Unset):
            bundle = self.bundle.to_dict()

        oracle: dict[str, Any] | Unset = UNSET
        if not isinstance(self.oracle, Unset):
            oracle = self.oracle.to_dict()

        oracle_schema = self.oracle_schema

        query_path = self.query_path

        workdir = self.workdir

        tags: list[str] | Unset = UNSET
        if not isinstance(self.tags, Unset):
            tags = self.tags

        resource_hints: dict[str, Any] | Unset = UNSET
        if not isinstance(self.resource_hints, Unset):
            resource_hints = self.resource_hints.to_dict()

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

        data_source: dict[str, Any] | Unset = UNSET
        if not isinstance(self.data_source, Unset):
            data_source = self.data_source.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "query": query,
                "runtime": runtime,
                "timeout_seconds": timeout_seconds,
            }
        )
        if bundle is not UNSET:
            field_dict["bundle"] = bundle
        if oracle is not UNSET:
            field_dict["oracle"] = oracle
        if oracle_schema is not UNSET:
            field_dict["oracle_schema"] = oracle_schema
        if query_path is not UNSET:
            field_dict["query_path"] = query_path
        if workdir is not UNSET:
            field_dict["workdir"] = workdir
        if tags is not UNSET:
            field_dict["tags"] = tags
        if resource_hints is not UNSET:
            field_dict["resource_hints"] = resource_hints
        if metadata is not UNSET:
            field_dict["metadata"] = metadata
        if data_source is not UNSET:
            field_dict["data_source"] = data_source

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.data_source import DataSource
        from ..models.env_runtime_spec import EnvRuntimeSpec
        from ..models.env_spec_metadata import EnvSpecMetadata
        from ..models.object_ref import ObjectRef
        from ..models.resource_spec import ResourceSpec

        d = dict(src_dict)
        query = ObjectRef.from_dict(d.pop("query"))

        runtime = EnvRuntimeSpec.from_dict(d.pop("runtime"))

        timeout_seconds = d.pop("timeout_seconds")

        _bundle = d.pop("bundle", UNSET)
        bundle: ObjectRef | Unset
        if isinstance(_bundle, Unset):
            bundle = UNSET
        else:
            bundle = ObjectRef.from_dict(_bundle)

        _oracle = d.pop("oracle", UNSET)
        oracle: ObjectRef | Unset
        if isinstance(_oracle, Unset):
            oracle = UNSET
        else:
            oracle = ObjectRef.from_dict(_oracle)

        oracle_schema = d.pop("oracle_schema", UNSET)

        query_path = d.pop("query_path", UNSET)

        workdir = d.pop("workdir", UNSET)

        tags = cast(list[str], d.pop("tags", UNSET))

        _resource_hints = d.pop("resource_hints", UNSET)
        resource_hints: ResourceSpec | Unset
        if isinstance(_resource_hints, Unset):
            resource_hints = UNSET
        else:
            resource_hints = ResourceSpec.from_dict(_resource_hints)

        _metadata = d.pop("metadata", UNSET)
        metadata: EnvSpecMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = EnvSpecMetadata.from_dict(_metadata)

        _data_source = d.pop("data_source", UNSET)
        data_source: DataSource | Unset
        if isinstance(_data_source, Unset):
            data_source = UNSET
        else:
            data_source = DataSource.from_dict(_data_source)

        env_spec = cls(
            query=query,
            runtime=runtime,
            timeout_seconds=timeout_seconds,
            bundle=bundle,
            oracle=oracle,
            oracle_schema=oracle_schema,
            query_path=query_path,
            workdir=workdir,
            tags=tags,
            resource_hints=resource_hints,
            metadata=metadata,
            data_source=data_source,
        )

        env_spec.additional_properties = d
        return env_spec

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
