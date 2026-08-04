from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.model_service_instance_check import ModelServiceInstanceCheck
from ...types import UNSET, Response, Unset


def _get_kwargs(
    service_id: str,
    instance_id: str,
    *,
    health_timeout: float | Unset = 2.0,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["health_timeout"] = health_timeout

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/openapi/v1/model-services/{service_id}/instances/{instance_id}/check".format(
            service_id=quote(str(service_id), safe=""),
            instance_id=quote(str(instance_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | ModelServiceInstanceCheck:
    if response.status_code == 200:
        response_200 = ModelServiceInstanceCheck.from_dict(response.json())

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | ModelServiceInstanceCheck]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    service_id: str,
    instance_id: str,
    *,
    client: AuthenticatedClient | Client,
    health_timeout: float | Unset = 2.0,
) -> Response[Error | ModelServiceInstanceCheck]:
    """Check one model service instance

     Checks the control-plane record, direct SGLang endpoint, OpenAI-compatible routes, Arena LLM Gateway
    route, AIS job, and local SGLang logs.

    Args:
        service_id (str):
        instance_id (str):
        health_timeout (float | Unset):  Default: 2.0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | ModelServiceInstanceCheck]
    """

    kwargs = _get_kwargs(
        service_id=service_id,
        instance_id=instance_id,
        health_timeout=health_timeout,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    service_id: str,
    instance_id: str,
    *,
    client: AuthenticatedClient | Client,
    health_timeout: float | Unset = 2.0,
) -> Error | ModelServiceInstanceCheck | None:
    """Check one model service instance

     Checks the control-plane record, direct SGLang endpoint, OpenAI-compatible routes, Arena LLM Gateway
    route, AIS job, and local SGLang logs.

    Args:
        service_id (str):
        instance_id (str):
        health_timeout (float | Unset):  Default: 2.0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | ModelServiceInstanceCheck
    """

    return sync_detailed(
        service_id=service_id,
        instance_id=instance_id,
        client=client,
        health_timeout=health_timeout,
    ).parsed


async def asyncio_detailed(
    service_id: str,
    instance_id: str,
    *,
    client: AuthenticatedClient | Client,
    health_timeout: float | Unset = 2.0,
) -> Response[Error | ModelServiceInstanceCheck]:
    """Check one model service instance

     Checks the control-plane record, direct SGLang endpoint, OpenAI-compatible routes, Arena LLM Gateway
    route, AIS job, and local SGLang logs.

    Args:
        service_id (str):
        instance_id (str):
        health_timeout (float | Unset):  Default: 2.0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | ModelServiceInstanceCheck]
    """

    kwargs = _get_kwargs(
        service_id=service_id,
        instance_id=instance_id,
        health_timeout=health_timeout,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    service_id: str,
    instance_id: str,
    *,
    client: AuthenticatedClient | Client,
    health_timeout: float | Unset = 2.0,
) -> Error | ModelServiceInstanceCheck | None:
    """Check one model service instance

     Checks the control-plane record, direct SGLang endpoint, OpenAI-compatible routes, Arena LLM Gateway
    route, AIS job, and local SGLang logs.

    Args:
        service_id (str):
        instance_id (str):
        health_timeout (float | Unset):  Default: 2.0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | ModelServiceInstanceCheck
    """

    return (
        await asyncio_detailed(
            service_id=service_id,
            instance_id=instance_id,
            client=client,
            health_timeout=health_timeout,
        )
    ).parsed
