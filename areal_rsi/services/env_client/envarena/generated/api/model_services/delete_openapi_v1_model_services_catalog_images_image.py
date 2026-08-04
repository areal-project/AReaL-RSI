from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.delete_openapi_v1_model_services_catalog_images_image_response_200 import (
    DeleteOpenapiV1ModelServicesCatalogImagesImageResponse200,
)
from ...models.error import Error
from ...types import Response


def _get_kwargs(
    image: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "delete",
        "url": "/openapi/v1/model-services/catalog/images/{image}".format(
            image=quote(str(image), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> DeleteOpenapiV1ModelServicesCatalogImagesImageResponse200 | Error:
    if response.status_code == 200:
        response_200 = (
            DeleteOpenapiV1ModelServicesCatalogImagesImageResponse200.from_dict(
                response.json()
            )
        )

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[DeleteOpenapiV1ModelServicesCatalogImagesImageResponse200 | Error]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    image: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[DeleteOpenapiV1ModelServicesCatalogImagesImageResponse200 | Error]:
    """Delete an unused AIS image from the model service catalog

    Args:
        image (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DeleteOpenapiV1ModelServicesCatalogImagesImageResponse200 | Error]
    """

    kwargs = _get_kwargs(
        image=image,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    image: str,
    *,
    client: AuthenticatedClient | Client,
) -> DeleteOpenapiV1ModelServicesCatalogImagesImageResponse200 | Error | None:
    """Delete an unused AIS image from the model service catalog

    Args:
        image (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DeleteOpenapiV1ModelServicesCatalogImagesImageResponse200 | Error
    """

    return sync_detailed(
        image=image,
        client=client,
    ).parsed


async def asyncio_detailed(
    image: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[DeleteOpenapiV1ModelServicesCatalogImagesImageResponse200 | Error]:
    """Delete an unused AIS image from the model service catalog

    Args:
        image (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DeleteOpenapiV1ModelServicesCatalogImagesImageResponse200 | Error]
    """

    kwargs = _get_kwargs(
        image=image,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    image: str,
    *,
    client: AuthenticatedClient | Client,
) -> DeleteOpenapiV1ModelServicesCatalogImagesImageResponse200 | Error | None:
    """Delete an unused AIS image from the model service catalog

    Args:
        image (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DeleteOpenapiV1ModelServicesCatalogImagesImageResponse200 | Error
    """

    return (
        await asyncio_detailed(
            image=image,
            client=client,
        )
    ).parsed
