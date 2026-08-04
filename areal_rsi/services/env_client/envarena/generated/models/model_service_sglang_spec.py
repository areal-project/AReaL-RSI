from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_service_sglang_spec_env import ModelServiceSglangSpecEnv


T = TypeVar("T", bound="ModelServiceSglangSpec")


@_attrs_define
class ModelServiceSglangSpec:
    """
    Attributes:
        tp (int | Unset):
        dp (int | Unset):
        pp_size (int | Unset):
        nccl_port (int | Unset):
        tokenizer_path (str | Unset):
        context_length (int | Unset):
        tool_call_parser (str | Unset):
        reasoning_parser (str | Unset):
        trust_remote_code (bool | Unset):
        chunked_prefill_size (int | Unset):
        dtype (str | Unset):
        mem_fraction_static (float | Unset):
        max_running_requests (int | Unset):
        mamba_full_memory_ratio (float | Unset):
        json_model_override_args (str | Unset):
        model_loader_extra_config (str | Unset):
        chat_template (str | Unset):
        watchdog_timeout (int | Unset):
        log_level (str | Unset):
        disable_chunked_prefix_cache (bool | Unset):
        disable_flashinfer_autotune (bool | Unset):
        enable_metrics (bool | Unset):
        enable_cache_report (bool | Unset):
        log_requests (bool | Unset):
        log_requests_level (int | Unset):
        runtime_prepare_command (str | Unset):
        env (ModelServiceSglangSpecEnv | Unset): Additional environment variables for SGLang. Variables managed by
            model-service are rejected, including MODEL_SERVICE_ID, MODEL_INSTANCE_ID, MODEL_PATH, SERVED_MODEL_NAME,
            CONTROL_BASE_URL, REGISTER_URL, STARTED_URL, REGISTER_TOKEN, SGLANG_PORT, SGLANG_PORT_INDEX, SGLANG_NNODES,
            SGLANG_NODE_RANK, SGLANG_DIST_INIT_ADDR, and SGLANG_LOG_DIR.
        extra_args (str | Unset):
    """

    tp: int | Unset = UNSET
    dp: int | Unset = UNSET
    pp_size: int | Unset = UNSET
    nccl_port: int | Unset = UNSET
    tokenizer_path: str | Unset = UNSET
    context_length: int | Unset = UNSET
    tool_call_parser: str | Unset = UNSET
    reasoning_parser: str | Unset = UNSET
    trust_remote_code: bool | Unset = UNSET
    chunked_prefill_size: int | Unset = UNSET
    dtype: str | Unset = UNSET
    mem_fraction_static: float | Unset = UNSET
    max_running_requests: int | Unset = UNSET
    mamba_full_memory_ratio: float | Unset = UNSET
    json_model_override_args: str | Unset = UNSET
    model_loader_extra_config: str | Unset = UNSET
    chat_template: str | Unset = UNSET
    watchdog_timeout: int | Unset = UNSET
    log_level: str | Unset = UNSET
    disable_chunked_prefix_cache: bool | Unset = UNSET
    disable_flashinfer_autotune: bool | Unset = UNSET
    enable_metrics: bool | Unset = UNSET
    enable_cache_report: bool | Unset = UNSET
    log_requests: bool | Unset = UNSET
    log_requests_level: int | Unset = UNSET
    runtime_prepare_command: str | Unset = UNSET
    env: ModelServiceSglangSpecEnv | Unset = UNSET
    extra_args: str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        tp = self.tp

        dp = self.dp

        pp_size = self.pp_size

        nccl_port = self.nccl_port

        tokenizer_path = self.tokenizer_path

        context_length = self.context_length

        tool_call_parser = self.tool_call_parser

        reasoning_parser = self.reasoning_parser

        trust_remote_code = self.trust_remote_code

        chunked_prefill_size = self.chunked_prefill_size

        dtype = self.dtype

        mem_fraction_static = self.mem_fraction_static

        max_running_requests = self.max_running_requests

        mamba_full_memory_ratio = self.mamba_full_memory_ratio

        json_model_override_args = self.json_model_override_args

        model_loader_extra_config = self.model_loader_extra_config

        chat_template = self.chat_template

        watchdog_timeout = self.watchdog_timeout

        log_level = self.log_level

        disable_chunked_prefix_cache = self.disable_chunked_prefix_cache

        disable_flashinfer_autotune = self.disable_flashinfer_autotune

        enable_metrics = self.enable_metrics

        enable_cache_report = self.enable_cache_report

        log_requests = self.log_requests

        log_requests_level = self.log_requests_level

        runtime_prepare_command = self.runtime_prepare_command

        env: dict[str, Any] | Unset = UNSET
        if not isinstance(self.env, Unset):
            env = self.env.to_dict()

        extra_args = self.extra_args

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if tp is not UNSET:
            field_dict["tp"] = tp
        if dp is not UNSET:
            field_dict["dp"] = dp
        if pp_size is not UNSET:
            field_dict["pp_size"] = pp_size
        if nccl_port is not UNSET:
            field_dict["nccl_port"] = nccl_port
        if tokenizer_path is not UNSET:
            field_dict["tokenizer_path"] = tokenizer_path
        if context_length is not UNSET:
            field_dict["context_length"] = context_length
        if tool_call_parser is not UNSET:
            field_dict["tool_call_parser"] = tool_call_parser
        if reasoning_parser is not UNSET:
            field_dict["reasoning_parser"] = reasoning_parser
        if trust_remote_code is not UNSET:
            field_dict["trust_remote_code"] = trust_remote_code
        if chunked_prefill_size is not UNSET:
            field_dict["chunked_prefill_size"] = chunked_prefill_size
        if dtype is not UNSET:
            field_dict["dtype"] = dtype
        if mem_fraction_static is not UNSET:
            field_dict["mem_fraction_static"] = mem_fraction_static
        if max_running_requests is not UNSET:
            field_dict["max_running_requests"] = max_running_requests
        if mamba_full_memory_ratio is not UNSET:
            field_dict["mamba_full_memory_ratio"] = mamba_full_memory_ratio
        if json_model_override_args is not UNSET:
            field_dict["json_model_override_args"] = json_model_override_args
        if model_loader_extra_config is not UNSET:
            field_dict["model_loader_extra_config"] = model_loader_extra_config
        if chat_template is not UNSET:
            field_dict["chat_template"] = chat_template
        if watchdog_timeout is not UNSET:
            field_dict["watchdog_timeout"] = watchdog_timeout
        if log_level is not UNSET:
            field_dict["log_level"] = log_level
        if disable_chunked_prefix_cache is not UNSET:
            field_dict["disable_chunked_prefix_cache"] = disable_chunked_prefix_cache
        if disable_flashinfer_autotune is not UNSET:
            field_dict["disable_flashinfer_autotune"] = disable_flashinfer_autotune
        if enable_metrics is not UNSET:
            field_dict["enable_metrics"] = enable_metrics
        if enable_cache_report is not UNSET:
            field_dict["enable_cache_report"] = enable_cache_report
        if log_requests is not UNSET:
            field_dict["log_requests"] = log_requests
        if log_requests_level is not UNSET:
            field_dict["log_requests_level"] = log_requests_level
        if runtime_prepare_command is not UNSET:
            field_dict["runtime_prepare_command"] = runtime_prepare_command
        if env is not UNSET:
            field_dict["env"] = env
        if extra_args is not UNSET:
            field_dict["extra_args"] = extra_args

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.model_service_sglang_spec_env import ModelServiceSglangSpecEnv

        d = dict(src_dict)
        tp = d.pop("tp", UNSET)

        dp = d.pop("dp", UNSET)

        pp_size = d.pop("pp_size", UNSET)

        nccl_port = d.pop("nccl_port", UNSET)

        tokenizer_path = d.pop("tokenizer_path", UNSET)

        context_length = d.pop("context_length", UNSET)

        tool_call_parser = d.pop("tool_call_parser", UNSET)

        reasoning_parser = d.pop("reasoning_parser", UNSET)

        trust_remote_code = d.pop("trust_remote_code", UNSET)

        chunked_prefill_size = d.pop("chunked_prefill_size", UNSET)

        dtype = d.pop("dtype", UNSET)

        mem_fraction_static = d.pop("mem_fraction_static", UNSET)

        max_running_requests = d.pop("max_running_requests", UNSET)

        mamba_full_memory_ratio = d.pop("mamba_full_memory_ratio", UNSET)

        json_model_override_args = d.pop("json_model_override_args", UNSET)

        model_loader_extra_config = d.pop("model_loader_extra_config", UNSET)

        chat_template = d.pop("chat_template", UNSET)

        watchdog_timeout = d.pop("watchdog_timeout", UNSET)

        log_level = d.pop("log_level", UNSET)

        disable_chunked_prefix_cache = d.pop("disable_chunked_prefix_cache", UNSET)

        disable_flashinfer_autotune = d.pop("disable_flashinfer_autotune", UNSET)

        enable_metrics = d.pop("enable_metrics", UNSET)

        enable_cache_report = d.pop("enable_cache_report", UNSET)

        log_requests = d.pop("log_requests", UNSET)

        log_requests_level = d.pop("log_requests_level", UNSET)

        runtime_prepare_command = d.pop("runtime_prepare_command", UNSET)

        _env = d.pop("env", UNSET)
        env: ModelServiceSglangSpecEnv | Unset
        if isinstance(_env, Unset):
            env = UNSET
        else:
            env = ModelServiceSglangSpecEnv.from_dict(_env)

        extra_args = d.pop("extra_args", UNSET)

        model_service_sglang_spec = cls(
            tp=tp,
            dp=dp,
            pp_size=pp_size,
            nccl_port=nccl_port,
            tokenizer_path=tokenizer_path,
            context_length=context_length,
            tool_call_parser=tool_call_parser,
            reasoning_parser=reasoning_parser,
            trust_remote_code=trust_remote_code,
            chunked_prefill_size=chunked_prefill_size,
            dtype=dtype,
            mem_fraction_static=mem_fraction_static,
            max_running_requests=max_running_requests,
            mamba_full_memory_ratio=mamba_full_memory_ratio,
            json_model_override_args=json_model_override_args,
            model_loader_extra_config=model_loader_extra_config,
            chat_template=chat_template,
            watchdog_timeout=watchdog_timeout,
            log_level=log_level,
            disable_chunked_prefix_cache=disable_chunked_prefix_cache,
            disable_flashinfer_autotune=disable_flashinfer_autotune,
            enable_metrics=enable_metrics,
            enable_cache_report=enable_cache_report,
            log_requests=log_requests,
            log_requests_level=log_requests_level,
            runtime_prepare_command=runtime_prepare_command,
            env=env,
            extra_args=extra_args,
        )

        return model_service_sglang_spec
