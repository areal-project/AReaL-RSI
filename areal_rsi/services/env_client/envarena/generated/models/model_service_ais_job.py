from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_service_ais_job_raw import ModelServiceAISJobRaw
    from ..models.model_service_ais_node import ModelServiceAISNode


T = TypeVar("T", bound="ModelServiceAISJob")


@_attrs_define
class ModelServiceAISJob:
    """
    Attributes:
        ais_job_id (str):
        status (str):
        message (str | Unset):
        detail_url (str | Unset):
        training_detail_url (str | Unset):
        log_urls (list[str] | Unset):
        trace_urls (list[str] | Unset):
        nodes (list[ModelServiceAISNode] | Unset):
        log_tail (list[str] | Unset):
        raw (ModelServiceAISJobRaw | Unset):
    """

    ais_job_id: str
    status: str
    message: str | Unset = UNSET
    detail_url: str | Unset = UNSET
    training_detail_url: str | Unset = UNSET
    log_urls: list[str] | Unset = UNSET
    trace_urls: list[str] | Unset = UNSET
    nodes: list[ModelServiceAISNode] | Unset = UNSET
    log_tail: list[str] | Unset = UNSET
    raw: ModelServiceAISJobRaw | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        ais_job_id = self.ais_job_id

        status = self.status

        message = self.message

        detail_url = self.detail_url

        training_detail_url = self.training_detail_url

        log_urls: list[str] | Unset = UNSET
        if not isinstance(self.log_urls, Unset):
            log_urls = self.log_urls

        trace_urls: list[str] | Unset = UNSET
        if not isinstance(self.trace_urls, Unset):
            trace_urls = self.trace_urls

        nodes: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.nodes, Unset):
            nodes = []
            for nodes_item_data in self.nodes:
                nodes_item = nodes_item_data.to_dict()
                nodes.append(nodes_item)

        log_tail: list[str] | Unset = UNSET
        if not isinstance(self.log_tail, Unset):
            log_tail = self.log_tail

        raw: dict[str, Any] | Unset = UNSET
        if not isinstance(self.raw, Unset):
            raw = self.raw.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "ais_job_id": ais_job_id,
                "status": status,
            }
        )
        if message is not UNSET:
            field_dict["message"] = message
        if detail_url is not UNSET:
            field_dict["detail_url"] = detail_url
        if training_detail_url is not UNSET:
            field_dict["training_detail_url"] = training_detail_url
        if log_urls is not UNSET:
            field_dict["log_urls"] = log_urls
        if trace_urls is not UNSET:
            field_dict["trace_urls"] = trace_urls
        if nodes is not UNSET:
            field_dict["nodes"] = nodes
        if log_tail is not UNSET:
            field_dict["log_tail"] = log_tail
        if raw is not UNSET:
            field_dict["raw"] = raw

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.model_service_ais_job_raw import ModelServiceAISJobRaw
        from ..models.model_service_ais_node import ModelServiceAISNode

        d = dict(src_dict)
        ais_job_id = d.pop("ais_job_id")

        status = d.pop("status")

        message = d.pop("message", UNSET)

        detail_url = d.pop("detail_url", UNSET)

        training_detail_url = d.pop("training_detail_url", UNSET)

        log_urls = cast(list[str], d.pop("log_urls", UNSET))

        trace_urls = cast(list[str], d.pop("trace_urls", UNSET))

        _nodes = d.pop("nodes", UNSET)
        nodes: list[ModelServiceAISNode] | Unset = UNSET
        if _nodes is not UNSET:
            nodes = []
            for nodes_item_data in _nodes:
                nodes_item = ModelServiceAISNode.from_dict(nodes_item_data)

                nodes.append(nodes_item)

        log_tail = cast(list[str], d.pop("log_tail", UNSET))

        _raw = d.pop("raw", UNSET)
        raw: ModelServiceAISJobRaw | Unset
        if isinstance(_raw, Unset):
            raw = UNSET
        else:
            raw = ModelServiceAISJobRaw.from_dict(_raw)

        model_service_ais_job = cls(
            ais_job_id=ais_job_id,
            status=status,
            message=message,
            detail_url=detail_url,
            training_detail_url=training_detail_url,
            log_urls=log_urls,
            trace_urls=trace_urls,
            nodes=nodes,
            log_tail=log_tail,
            raw=raw,
        )

        model_service_ais_job.additional_properties = d
        return model_service_ais_job

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
