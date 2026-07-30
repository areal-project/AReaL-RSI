from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.aggregation_spec_failed_task_policy import AggregationSpecFailedTaskPolicy
from ..models.aggregation_spec_method import AggregationSpecMethod
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.aggregation_spec_params import AggregationSpecParams


T = TypeVar("T", bound="AggregationSpec")


@_attrs_define
class AggregationSpec:
    """
    Attributes:
        method (AggregationSpecMethod):
        failed_task_policy (AggregationSpecFailedTaskPolicy | Unset):  Default:
            AggregationSpecFailedTaskPolicy.FAILED_AS_ZERO.
        min_success_rate (float | Unset):
        pass_threshold (float | Unset):
        params (AggregationSpecParams | Unset):
    """

    method: AggregationSpecMethod
    failed_task_policy: AggregationSpecFailedTaskPolicy | Unset = (
        AggregationSpecFailedTaskPolicy.FAILED_AS_ZERO
    )
    min_success_rate: float | Unset = UNSET
    pass_threshold: float | Unset = UNSET
    params: AggregationSpecParams | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        method = self.method.value

        failed_task_policy: str | Unset = UNSET
        if not isinstance(self.failed_task_policy, Unset):
            failed_task_policy = self.failed_task_policy.value

        min_success_rate = self.min_success_rate

        pass_threshold = self.pass_threshold

        params: dict[str, Any] | Unset = UNSET
        if not isinstance(self.params, Unset):
            params = self.params.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "method": method,
            }
        )
        if failed_task_policy is not UNSET:
            field_dict["failed_task_policy"] = failed_task_policy
        if min_success_rate is not UNSET:
            field_dict["min_success_rate"] = min_success_rate
        if pass_threshold is not UNSET:
            field_dict["pass_threshold"] = pass_threshold
        if params is not UNSET:
            field_dict["params"] = params

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.aggregation_spec_params import AggregationSpecParams

        d = dict(src_dict)
        method = AggregationSpecMethod(d.pop("method"))

        _failed_task_policy = d.pop("failed_task_policy", UNSET)
        failed_task_policy: AggregationSpecFailedTaskPolicy | Unset
        if isinstance(_failed_task_policy, Unset):
            failed_task_policy = UNSET
        else:
            failed_task_policy = AggregationSpecFailedTaskPolicy(_failed_task_policy)

        min_success_rate = d.pop("min_success_rate", UNSET)

        pass_threshold = d.pop("pass_threshold", UNSET)

        _params = d.pop("params", UNSET)
        params: AggregationSpecParams | Unset
        if isinstance(_params, Unset):
            params = UNSET
        else:
            params = AggregationSpecParams.from_dict(_params)

        aggregation_spec = cls(
            method=method,
            failed_task_policy=failed_task_policy,
            min_success_rate=min_success_rate,
            pass_threshold=pass_threshold,
            params=params,
        )

        aggregation_spec.additional_properties = d
        return aggregation_spec

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
