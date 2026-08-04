from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.model_service_catalog_parameters import ModelServiceCatalogParameters
from ...types import UNSET, Response, Unset


def _get_kwargs(
    model_id: str,
    *,
    persist: bool | Unset = False,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["persist"] = persist

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/openapi/v1/model-services/catalog/models/{model_id}/parameters".format(
            model_id=quote(str(model_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | ModelServiceCatalogParameters:
    if response.status_code == 200:
        response_200 = ModelServiceCatalogParameters.from_dict(response.json())

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | ModelServiceCatalogParameters]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    model_id: str,
    *,
    client: AuthenticatedClient | Client,
    persist: bool | Unset = False,
) -> Response[Error | ModelServiceCatalogParameters]:
    """Count parameter metadata for one catalog model

    Args:
        model_id (str):
        persist (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | ModelServiceCatalogParameters]
    """

    kwargs = _get_kwargs(
        model_id=model_id,
        persist=persist,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    model_id: str,
    *,
    client: AuthenticatedClient | Client,
    persist: bool | Unset = False,
) -> Error | ModelServiceCatalogParameters | None:
    """Count parameter metadata for one catalog model

    Args:
        model_id (str):
        persist (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | ModelServiceCatalogParameters
    """

    return sync_detailed(
        model_id=model_id,
        client=client,
        persist=persist,
    ).parsed


async def asyncio_detailed(
    model_id: str,
    *,
    client: AuthenticatedClient | Client,
    persist: bool | Unset = False,
) -> Response[Error | ModelServiceCatalogParameters]:
    """Count parameter metadata for one catalog model

    Args:
        model_id (str):
        persist (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | ModelServiceCatalogParameters]
    """

    kwargs = _get_kwargs(
        model_id=model_id,
        persist=persist,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    model_id: str,
    *,
    client: AuthenticatedClient | Client,
    persist: bool | Unset = False,
) -> Error | ModelServiceCatalogParameters | None:
    """Count parameter metadata for one catalog model

    Args:
        model_id (str):
        persist (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | ModelServiceCatalogParameters
    """

    return (
        await asyncio_detailed(
            model_id=model_id,
            client=client,
            persist=persist,
        )
    ).parsed
