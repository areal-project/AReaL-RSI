import json
import warnings
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import pytest

from areal_rsi.services import EnvArenaClient, EnvArenaConfig, EnvArenaHTTPError


REAL_ENVARENA_BASE_URL = "http://33.214.157.35:8080"
REAL_ENVARENA_API_KEY = "areal"
REAL_MODEL_SERVICE_BASE_MODEL_ID = "bailing-v3"
REAL_MODEL_SERVICE_MODEL_PATH = "/storage/openpsi/models/sft_ling_flash_v3_fusion_v0626all_v0627_newckpt"
REAL_MODEL_REGISTRY_GATEWAY_MODEL_ID = "areal-rsi-real-reg-26a3a0d9"
REAL_MODEL_REGISTRY_CLEAN = False
REAL_MODEL_REGISTRY_READY_TIMEOUT_SECONDS = 1800
REAL_MODEL_REGISTRY_READY_POLL_SECONDS = 15
MAX_PRINT_DICT_ITEMS = 40
MAX_PRINT_LIST_ITEMS = 2
MAX_PRINT_STRING_CHARS = 500
REGISTRY_KINDS = ("env", "harness", "reward", "benchmark", "model", "skill", "tool")


def _is_sensitive_key(key: object) -> bool:
    normalized = str(key).lower()
    return any(marker in normalized for marker in ("api_key", "token", "secret", "password", "credential"))


def _compact(value: Any) -> Any:
    if isinstance(value, Mapping):
        compacted: dict[str, Any] = {}
        for index, (key, nested) in enumerate(value.items()):
            if index >= MAX_PRINT_DICT_ITEMS:
                compacted["__truncated_keys__"] = f"{len(value) - MAX_PRINT_DICT_ITEMS} more keys"
                break
            compacted[str(key)] = "<redacted>" if _is_sensitive_key(key) else _compact(nested)
        return compacted
    if isinstance(value, list):
        head = [_compact(item) for item in value[:MAX_PRINT_LIST_ITEMS]]
        if len(value) > MAX_PRINT_LIST_ITEMS:
            head.append({"__truncated__": f"{len(value) - MAX_PRINT_LIST_ITEMS} more items"})
        return head
    if isinstance(value, str) and len(value) > MAX_PRINT_STRING_CHARS:
        return f"{value[:MAX_PRINT_STRING_CHARS]}...<truncated {len(value) - MAX_PRINT_STRING_CHARS} chars>"
    return value


def _pretty(name: str, payload: Any) -> None:
    print(f"\n===== {name} =====")
    print(json.dumps(_compact(payload), indent=2, ensure_ascii=False, sort_keys=True, default=str))


def _pretty_optional(name: str, callback) -> None:
    try:
        _pretty(name, callback())
    except EnvArenaHTTPError as exc:
        _pretty(name, {"warning": "request failed", "status_code": exc.status_code, "detail": exc.detail})


def _instance_ids(payload: Mapping[str, Any]) -> set[str]:
    instances = payload.get("instances")
    if not isinstance(instances, list):
        return set()
    return {
        str(instance.get("instance_id"))
        for instance in instances
        if isinstance(instance, Mapping) and instance.get("instance_id")
    }


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _model_service_from_result(result: Mapping[str, Any]) -> dict[str, Any]:
    service = _mapping(result.get("model_service"))
    if service:
        return service
    return _mapping(_mapping(result.get("asset")).get("model_service"))


def _find_model_service_by_name(client: EnvArenaClient, served_model_name: str) -> dict[str, Any]:
    for service in client.list_model_services():
        if isinstance(service, Mapping) and str(service.get("served_model_name") or "") == served_model_name:
            return dict(service)
    return {}


def _stop_model_service_instances(
    client: EnvArenaClient,
    service_id: str,
    instance_ids: set[str],
) -> None:
    for instance_id in sorted(instance_ids):
        try:
            stopped_instance = client.stop_model_service_instance_and_wait(service_id, instance_id)
            _pretty(f"stopped model service instance {service_id}/{instance_id}", stopped_instance)
        except Exception as exc:
            warnings.warn(
                f"Failed to stop real EnvArena model service instance {service_id}/{instance_id}: {exc}",
                RuntimeWarning,
            )


def test_real_envarena_get_endpoints_pretty_print():
    client = EnvArenaClient(
        EnvArenaConfig(
            control_base_url=REAL_ENVARENA_BASE_URL,
            api_token=REAL_ENVARENA_API_KEY,
            timeout_seconds=30,
        )
    )
    try:
        _pretty("runtime llm models", client.list_models(disable_stream=True))
        _pretty("active streams", client.list_streams(status="ACTIVE"))
        _pretty_optional("model services", client.list_model_services)
        _pretty("online harnesses", client.list_online_harnesses())

        for kind in REGISTRY_KINDS:
            page = client.list_registry_entity_page(kind, limit=5, offset=0)
            _pretty(f"{kind} first page", page)
            items = page.get("items")
            if not isinstance(items, list) or not items:
                continue
            key = str(items[0].get("key") or "")
            if not key:
                continue
            _pretty(f"{kind} entity {key}", client.get_registry_entity(kind, key))
            _pretty(f"{kind} published versions {key}", client.list_registry_versions(kind, key, status="PUBLISHED"))

    finally:
        client.close()


def test_real_envarena_create_model_service_from_base_model_and_query_status():
    client = EnvArenaClient(
        EnvArenaConfig(
            control_base_url=REAL_ENVARENA_BASE_URL,
            api_token=REAL_ENVARENA_API_KEY,
            timeout_seconds=30,
        )
    )
    service_id = ""
    instance_ids: set[str] = set()
    try:
        served_model_name = f"areal-rsi-real-test-{uuid4().hex[:8]}"
        service = client.create_model_service(
            base_model_id=REAL_MODEL_SERVICE_BASE_MODEL_ID,
            served_model_name=served_model_name,
            model_path=REAL_MODEL_SERVICE_MODEL_PATH,
            metadata={
                "source": "areal-rsi-real-test",
                "base_model_id": REAL_MODEL_SERVICE_BASE_MODEL_ID,
            },
        )
        _pretty("created model service", service)

        service_id = str(service.get("service_id") or "")
        assert service_id, "create_model_service should return service_id for status queries"

        status = client.get_model_service(service_id)
        _pretty(f"model service status {service_id}", status)
        assert str(status.get("service_id") or service_id) == service_id
        instance_ids = _instance_ids(service) | _instance_ids(status)
        assert instance_ids, "create_model_service should return instance_id values for instance lifecycle operations"

    finally:
        if service_id and not instance_ids:
            try:
                instance_ids = _instance_ids(client.get_model_service(service_id))
            except Exception as exc:
                warnings.warn(
                    f"Failed to query real EnvArena model service {service_id} for instance cleanup: {exc}",
                    RuntimeWarning,
                )
        for instance_id in sorted(instance_ids):
            try:
                stopped_instance = client.stop_model_service_instance_and_wait(service_id, instance_id)
                _pretty(f"stopped model service instance {service_id}/{instance_id}", stopped_instance)
            except Exception as exc:
                warnings.warn(
                    f"Failed to stop real EnvArena model service instance {service_id}/{instance_id}: {exc}",
                    RuntimeWarning,
                )
        client.close()


def test_real_envarena_register_model_registry_from_gateway_model():
    client = EnvArenaClient(
        EnvArenaConfig(
            control_base_url=REAL_ENVARENA_BASE_URL,
            api_token=REAL_ENVARENA_API_KEY,
            timeout_seconds=60,
        )
    )
    suffix = uuid4().hex[:8]
    model_key = f"areal-rsi-real-direct-{suffix}"
    model_version = "1.0.0"
    try:
        _pretty(
            "register model registry config",
            {
                "gateway_model_id": REAL_MODEL_REGISTRY_GATEWAY_MODEL_ID,
                "model_ref": f"{model_key}@{model_version}",
                "route": "/openapi/v1/models-register",
            },
        )
        result = client.register_agent_model(
            REAL_MODEL_REGISTRY_GATEWAY_MODEL_ID,
            key=model_key,
            version=model_version,
            name=f"AReaL RSI direct register test {suffix}",
            description="Created by direct EnvArenaClient model registry registration test.",
            tags=["areal-rsi-real-test", "model-register"],
            metadata={
                "source": "areal-rsi-real-test",
                "gateway_model_id": REAL_MODEL_REGISTRY_GATEWAY_MODEL_ID,
            },
            publish=True,
        )
        _pretty("registered model version", result)

        assert result["key"] == model_key
        assert result["version"] == model_version
        assert result["spec"]["model_name"] == REAL_MODEL_REGISTRY_GATEWAY_MODEL_ID

        model_entity = client.get_model(model_key)
        _pretty("registered model entity", model_entity)
        assert model_entity["key"] == model_key

        model_version_payload = client.get_model_version(model_key, model_version)
        _pretty("queried model version", model_version_payload)
        assert model_version_payload["key"] == model_key
        assert model_version_payload["version"] == model_version
        assert model_version_payload["spec"]["model_name"] == REAL_MODEL_REGISTRY_GATEWAY_MODEL_ID

    finally:
        client.close()


@pytest.mark.parametrize("clean", [REAL_MODEL_REGISTRY_CLEAN])
def test_real_envarena_ensure_model_registry_for_ckpt_full_chain(clean: bool):
    client = EnvArenaClient(
        EnvArenaConfig(
            control_base_url=REAL_ENVARENA_BASE_URL,
            api_token=REAL_ENVARENA_API_KEY,
            timeout_seconds=60,
        )
    )
    suffix = uuid4().hex[:8]
    served_model_name = f"areal-rsi-real-reg-{suffix}"
    model_key = f"areal-rsi-real-reg-{suffix}"
    model_version = "1.0.0"
    service_id = ""
    instance_ids: set[str] = set()
    try:
        _pretty(
            "ensure model registry for ckpt config",
            {
                "clean": clean,
                "base_model_id": REAL_MODEL_SERVICE_BASE_MODEL_ID,
                "model_path": REAL_MODEL_SERVICE_MODEL_PATH,
                "served_model_name": served_model_name,
                "model_ref": f"{model_key}@{model_version}",
                "ready_timeout_seconds": REAL_MODEL_REGISTRY_READY_TIMEOUT_SECONDS,
                "ready_poll_seconds": REAL_MODEL_REGISTRY_READY_POLL_SECONDS,
            },
        )
        result = client.ensure_model_registry_for_ckpt(
            {
                "model_path": REAL_MODEL_SERVICE_MODEL_PATH,
                "served_model_name": served_model_name,
                "base_model_id": REAL_MODEL_SERVICE_BASE_MODEL_ID,
            },
            key=model_key,
            version=model_version,
            name=f"AReaL RSI real registry test {suffix}",
            description="Created by AReaL-RSI real EnvArenaClient full-chain test.",
            tags=["areal-rsi-real-test", "model-service"],
            metadata={
                "source": "areal-rsi-real-test",
                "base_model_id": REAL_MODEL_SERVICE_BASE_MODEL_ID,
                "served_model_name": served_model_name,
            },
            job_config={
                "model_service": {"enabled": True},
                "model_registry": {
                    "profile": "dual",
                    "metadata": {
                        "model_path": REAL_MODEL_SERVICE_MODEL_PATH,
                    },
                },
            },
            wait_ready=True,
            ready_timeout_seconds=REAL_MODEL_REGISTRY_READY_TIMEOUT_SECONDS,
            ready_poll_seconds=REAL_MODEL_REGISTRY_READY_POLL_SECONDS,
        )
        _pretty("ensure model registry for ckpt result", result)

        assert result["model_name"] == served_model_name
        assert result["registry_ref"] == {"key": model_key, "version": model_version}
        assert result["model_register"]["key"] == model_key
        assert result["model_register"]["version"] == model_version

        service = _model_service_from_result(result)
        _pretty("ready model service from registry result", service)
        service_id = str(service.get("service_id") or "")
        instance_ids = _instance_ids(service)
        assert service_id, "ensure_model_registry_for_ckpt should expose the model service id"
        assert instance_ids, "ensure_model_registry_for_ckpt should expose model service instance ids"
        assert str(service.get("served_model_name") or "") == served_model_name
        assert str(service.get("status") or "") == "READY"

        model_entity = client.get_model(model_key)
        _pretty("registered model entity", model_entity)
        assert model_entity["key"] == model_key

        model_version_payload = client.get_model_version(model_key, model_version)
        _pretty("registered model version", model_version_payload)
        assert model_version_payload["key"] == model_key
        assert model_version_payload["version"] == model_version
        assert model_version_payload["spec"]["model_name"] == served_model_name

    finally:
        if clean:
            if not service_id:
                service = _find_model_service_by_name(client, served_model_name)
                service_id = str(service.get("service_id") or "")
                instance_ids |= _instance_ids(service)
            if service_id and not instance_ids:
                try:
                    instance_ids = _instance_ids(client.get_model_service(service_id))
                except Exception as exc:
                    warnings.warn(
                        f"Failed to query real EnvArena model service {service_id} for instance cleanup: {exc}",
                        RuntimeWarning,
                    )
            if service_id:
                _stop_model_service_instances(client, service_id, instance_ids)
        else:
            _pretty(
                "cleanup skipped",
                {
                    "clean": clean,
                    "served_model_name": served_model_name,
                    "service_id": service_id,
                    "instance_ids": sorted(instance_ids),
                    "model_ref": f"{model_key}@{model_version}",
                },
            )
        client.close()
