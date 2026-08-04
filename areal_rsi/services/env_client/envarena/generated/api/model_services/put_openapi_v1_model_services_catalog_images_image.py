from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.model_service_catalog_image import ModelServiceCatalogImage
from ...models.model_service_catalog_image_request import (
    ModelServiceCatalogImageRequest,
)
from ...types import Response


def _get_kwargs(
    image: str,
    *,
    body: ModelServiceCatalogImageRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "put",
        "url": "/openapi/v1/model-services/catalog/images/{image}".format(
            image=quote(str(image), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | ModelServiceCatalogImage:
    if response.status_code == 200:
        response_200 = ModelServiceCatalogImage.from_dict(response.json())

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | ModelServiceCatalogImage]:
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
    body: ModelServiceCatalogImageRequest,
) -> Response[Error | ModelServiceCatalogImage]:
    """Rename or update an AIS image in the model service catalog

    Args:
        image (str):
        body (ModelServiceCatalogImageRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | ModelServiceCatalogImage]
    """

    kwargs = _get_kwargs(
        image=image,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    image: str,
    *,
    client: AuthenticatedClient | Client,
    body: ModelServiceCatalogImageRequest,
) -> Error | ModelServiceCatalogImage | None:
    """Rename or update an AIS image in the model service catalog

    Args:
        image (str):
        body (ModelServiceCatalogImageRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | ModelServiceCatalogImage
    """

    return sync_detailed(
        image=image,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    image: str,
    *,
    client: AuthenticatedClient | Client,
    body: ModelServiceCatalogImageRequest,
) -> Response[Error | ModelServiceCatalogImage]:
    """Rename or update an AIS image in the model service catalog

    Args:
        image (str):
        body (ModelServiceCatalogImageRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | ModelServiceCatalogImage]
    """

    kwargs = _get_kwargs(
        image=image,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    image: str,
    *,
    client: AuthenticatedClient | Client,
    body: ModelServiceCatalogImageRequest,
) -> Error | ModelServiceCatalogImage | None:
    """Rename or update an AIS image in the model service catalog

    Args:
        image (str):
        body (ModelServiceCatalogImageRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | ModelServiceCatalogImage
    """

    return (
        await asyncio_detailed(
            image=image,
            client=client,
            body=body,
        )
    ).parsed
