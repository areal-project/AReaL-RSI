from http import HTTPStatus
from typing import Any

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.model_service_catalog_parameters_response import (
    ModelServiceCatalogParametersResponse,
)
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    persist: bool | Unset = False,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["persist"] = persist

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/openapi/v1/model-services/catalog/parameters",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | ModelServiceCatalogParametersResponse:
    if response.status_code == 200:
        response_200 = ModelServiceCatalogParametersResponse.from_dict(response.json())

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | ModelServiceCatalogParametersResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    persist: bool | Unset = False,
) -> Response[Error | ModelServiceCatalogParametersResponse]:
    """Count parameter metadata for all catalog models

    Args:
        persist (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | ModelServiceCatalogParametersResponse]
    """

    kwargs = _get_kwargs(
        persist=persist,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    persist: bool | Unset = False,
) -> Error | ModelServiceCatalogParametersResponse | None:
    """Count parameter metadata for all catalog models

    Args:
        persist (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | ModelServiceCatalogParametersResponse
    """

    return sync_detailed(
        client=client,
        persist=persist,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    persist: bool | Unset = False,
) -> Response[Error | ModelServiceCatalogParametersResponse]:
    """Count parameter metadata for all catalog models

    Args:
        persist (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | ModelServiceCatalogParametersResponse]
    """

    kwargs = _get_kwargs(
        persist=persist,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    persist: bool | Unset = False,
) -> Error | ModelServiceCatalogParametersResponse | None:
    """Count parameter metadata for all catalog models

    Args:
        persist (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | ModelServiceCatalogParametersResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            persist=persist,
        )
    ).parsed
