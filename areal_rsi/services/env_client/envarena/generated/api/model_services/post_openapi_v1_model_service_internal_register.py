from http import HTTPStatus
from typing import Any

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.model_service import ModelService
from ...models.model_service_register_instance_request import (
    ModelServiceRegisterInstanceRequest,
)
from ...types import Response


def _get_kwargs(
    *,
    body: ModelServiceRegisterInstanceRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/openapi/v1/model-service/internal/register",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | ModelService:
    if response.status_code == 200:
        response_200 = ModelService.from_dict(response.json())

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | ModelService]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: ModelServiceRegisterInstanceRequest,
) -> Response[Error | ModelService]:
    """Internal callback registering a ready SGLang endpoint

     Internal endpoint called by AIS jobs after SGLang health and model probes pass. Authorization bearer
    value must match ARENA_MODEL_SERVICE_REGISTER_TOKEN.

    Args:
        body (ModelServiceRegisterInstanceRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | ModelService]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    body: ModelServiceRegisterInstanceRequest,
) -> Error | ModelService | None:
    """Internal callback registering a ready SGLang endpoint

     Internal endpoint called by AIS jobs after SGLang health and model probes pass. Authorization bearer
    value must match ARENA_MODEL_SERVICE_REGISTER_TOKEN.

    Args:
        body (ModelServiceRegisterInstanceRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | ModelService
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: ModelServiceRegisterInstanceRequest,
) -> Response[Error | ModelService]:
    """Internal callback registering a ready SGLang endpoint

     Internal endpoint called by AIS jobs after SGLang health and model probes pass. Authorization bearer
    value must match ARENA_MODEL_SERVICE_REGISTER_TOKEN.

    Args:
        body (ModelServiceRegisterInstanceRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | ModelService]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: ModelServiceRegisterInstanceRequest,
) -> Error | ModelService | None:
    """Internal callback registering a ready SGLang endpoint

     Internal endpoint called by AIS jobs after SGLang health and model probes pass. Authorization bearer
    value must match ARENA_MODEL_SERVICE_REGISTER_TOKEN.

    Args:
        body (ModelServiceRegisterInstanceRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | ModelService
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
