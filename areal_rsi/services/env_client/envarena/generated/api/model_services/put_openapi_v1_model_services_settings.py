from http import HTTPStatus
from typing import Any

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.model_service_settings import ModelServiceSettings
from ...models.update_model_service_settings_request import (
    UpdateModelServiceSettingsRequest,
)
from ...types import Response


def _get_kwargs(
    *,
    body: UpdateModelServiceSettingsRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "put",
        "url": "/openapi/v1/model-services/settings",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | ModelServiceSettings:
    if response.status_code == 200:
        response_200 = ModelServiceSettings.from_dict(response.json())

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | ModelServiceSettings]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: UpdateModelServiceSettingsRequest,
) -> Response[Error | ModelServiceSettings]:
    """Update model-service runtime settings (root only)

     Updates DB-backed model-service defaults, including Go-side bridge/register tokens.
    MODEL_AIS_BRIDGE_ADDR remains a bridge process startup setting.

    Args:
        body (UpdateModelServiceSettingsRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | ModelServiceSettings]
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
    body: UpdateModelServiceSettingsRequest,
) -> Error | ModelServiceSettings | None:
    """Update model-service runtime settings (root only)

     Updates DB-backed model-service defaults, including Go-side bridge/register tokens.
    MODEL_AIS_BRIDGE_ADDR remains a bridge process startup setting.

    Args:
        body (UpdateModelServiceSettingsRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | ModelServiceSettings
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: UpdateModelServiceSettingsRequest,
) -> Response[Error | ModelServiceSettings]:
    """Update model-service runtime settings (root only)

     Updates DB-backed model-service defaults, including Go-side bridge/register tokens.
    MODEL_AIS_BRIDGE_ADDR remains a bridge process startup setting.

    Args:
        body (UpdateModelServiceSettingsRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | ModelServiceSettings]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: UpdateModelServiceSettingsRequest,
) -> Error | ModelServiceSettings | None:
    """Update model-service runtime settings (root only)

     Updates DB-backed model-service defaults, including Go-side bridge/register tokens.
    MODEL_AIS_BRIDGE_ADDR remains a bridge process startup setting.

    Args:
        body (UpdateModelServiceSettingsRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | ModelServiceSettings
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
