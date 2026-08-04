from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.delete_openapi_v1_model_services_catalog_models_model_id_response_200 import (
    DeleteOpenapiV1ModelServicesCatalogModelsModelIdResponse200,
)
from ...models.error import Error
from ...types import Response


def _get_kwargs(
    model_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "delete",
        "url": "/openapi/v1/model-services/catalog/models/{model_id}".format(
            model_id=quote(str(model_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> DeleteOpenapiV1ModelServicesCatalogModelsModelIdResponse200 | Error:
    if response.status_code == 200:
        response_200 = (
            DeleteOpenapiV1ModelServicesCatalogModelsModelIdResponse200.from_dict(
                response.json()
            )
        )

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[DeleteOpenapiV1ModelServicesCatalogModelsModelIdResponse200 | Error]:
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
) -> Response[DeleteOpenapiV1ModelServicesCatalogModelsModelIdResponse200 | Error]:
    """Delete a model service catalog model

    Args:
        model_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DeleteOpenapiV1ModelServicesCatalogModelsModelIdResponse200 | Error]
    """

    kwargs = _get_kwargs(
        model_id=model_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    model_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> DeleteOpenapiV1ModelServicesCatalogModelsModelIdResponse200 | Error | None:
    """Delete a model service catalog model

    Args:
        model_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DeleteOpenapiV1ModelServicesCatalogModelsModelIdResponse200 | Error
    """

    return sync_detailed(
        model_id=model_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    model_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[DeleteOpenapiV1ModelServicesCatalogModelsModelIdResponse200 | Error]:
    """Delete a model service catalog model

    Args:
        model_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DeleteOpenapiV1ModelServicesCatalogModelsModelIdResponse200 | Error]
    """

    kwargs = _get_kwargs(
        model_id=model_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    model_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> DeleteOpenapiV1ModelServicesCatalogModelsModelIdResponse200 | Error | None:
    """Delete a model service catalog model

    Args:
        model_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DeleteOpenapiV1ModelServicesCatalogModelsModelIdResponse200 | Error
    """

    return (
        await asyncio_detailed(
            model_id=model_id,
            client=client,
        )
    ).parsed
