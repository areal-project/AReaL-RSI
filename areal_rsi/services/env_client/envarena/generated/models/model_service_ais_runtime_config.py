from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_service_data_store_config import ModelServiceDataStoreConfig


T = TypeVar("T", bound="ModelServiceAISRuntimeConfig")


@_attrs_define
class ModelServiceAISRuntimeConfig:
    """
    Attributes:
        login_name (str | Unset):  Default: 'aistudio'.
        app_name (str | Unset):  Default: 'areals3'.
        group_id (int | Unset):
        priority (str | Unset):  Default: 'low'.
        runtime (str | Unset):  Default: 'pytorch'.
        retry_max_attempt (int | Unset):  Default: 3.
        job_script_path (str | Unset):
        default_image (str | Unset):
        pypai_target_dir (str | Unset): Optional PyPAI local target directory for generated job files. Empty keeps PyPAI
            default.
        data_stores (list[ModelServiceDataStoreConfig] | Unset):
    """

    login_name: str | Unset = "aistudio"
    app_name: str | Unset = "areals3"
    group_id: int | Unset = UNSET
    priority: str | Unset = "low"
    runtime: str | Unset = "pytorch"
    retry_max_attempt: int | Unset = 3
    job_script_path: str | Unset = UNSET
    default_image: str | Unset = UNSET
    pypai_target_dir: str | Unset = UNSET
    data_stores: list[ModelServiceDataStoreConfig] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        login_name = self.login_name

        app_name = self.app_name

        group_id = self.group_id

        priority = self.priority

        runtime = self.runtime

        retry_max_attempt = self.retry_max_attempt

        job_script_path = self.job_script_path

        default_image = self.default_image

        pypai_target_dir = self.pypai_target_dir

        data_stores: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.data_stores, Unset):
            data_stores = []
            for data_stores_item_data in self.data_stores:
                data_stores_item = data_stores_item_data.to_dict()
                data_stores.append(data_stores_item)

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if login_name is not UNSET:
            field_dict["login_name"] = login_name
        if app_name is not UNSET:
            field_dict["app_name"] = app_name
        if group_id is not UNSET:
            field_dict["group_id"] = group_id
        if priority is not UNSET:
            field_dict["priority"] = priority
        if runtime is not UNSET:
            field_dict["runtime"] = runtime
        if retry_max_attempt is not UNSET:
            field_dict["retry_max_attempt"] = retry_max_attempt
        if job_script_path is not UNSET:
            field_dict["job_script_path"] = job_script_path
        if default_image is not UNSET:
            field_dict["default_image"] = default_image
        if pypai_target_dir is not UNSET:
            field_dict["pypai_target_dir"] = pypai_target_dir
        if data_stores is not UNSET:
            field_dict["data_stores"] = data_stores

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.model_service_data_store_config import ModelServiceDataStoreConfig

        d = dict(src_dict)
        login_name = d.pop("login_name", UNSET)

        app_name = d.pop("app_name", UNSET)

        group_id = d.pop("group_id", UNSET)

        priority = d.pop("priority", UNSET)

        runtime = d.pop("runtime", UNSET)

        retry_max_attempt = d.pop("retry_max_attempt", UNSET)

        job_script_path = d.pop("job_script_path", UNSET)

        default_image = d.pop("default_image", UNSET)

        pypai_target_dir = d.pop("pypai_target_dir", UNSET)

        _data_stores = d.pop("data_stores", UNSET)
        data_stores: list[ModelServiceDataStoreConfig] | Unset = UNSET
        if _data_stores is not UNSET:
            data_stores = []
            for data_stores_item_data in _data_stores:
                data_stores_item = ModelServiceDataStoreConfig.from_dict(
                    data_stores_item_data
                )

                data_stores.append(data_stores_item)

        model_service_ais_runtime_config = cls(
            login_name=login_name,
            app_name=app_name,
            group_id=group_id,
            priority=priority,
            runtime=runtime,
            retry_max_attempt=retry_max_attempt,
            job_script_path=job_script_path,
            default_image=default_image,
            pypai_target_dir=pypai_target_dir,
            data_stores=data_stores,
        )

        return model_service_ais_runtime_config
