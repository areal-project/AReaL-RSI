from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.create_model_version_request import CreateModelVersionRequest
from ...models.error import Error
from ...models.model_version import ModelVersion
from ...types import Response


def _get_kwargs(
    model_key: str,
    *,
    body: CreateModelVersionRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/openapi/v1/models/{model_key}/versions".format(
            model_key=quote(str(model_key), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | ModelVersion:
    if response.status_code == 201:
        response_201 = ModelVersion.from_dict(response.json())

        return response_201

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | ModelVersion]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    model_key: str,
    *,
    client: AuthenticatedClient | Client,
    body: CreateModelVersionRequest,
) -> Response[Error | ModelVersion]:
    """Create a draft Model version

    Args:
        model_key (str):
        body (CreateModelVersionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | ModelVersion]
    """

    kwargs = _get_kwargs(
        model_key=model_key,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    model_key: str,
    *,
    client: AuthenticatedClient | Client,
    body: CreateModelVersionRequest,
) -> Error | ModelVersion | None:
    """Create a draft Model version

    Args:
        model_key (str):
        body (CreateModelVersionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | ModelVersion
    """

    return sync_detailed(
        model_key=model_key,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    model_key: str,
    *,
    client: AuthenticatedClient | Client,
    body: CreateModelVersionRequest,
) -> Response[Error | ModelVersion]:
    """Create a draft Model version

    Args:
        model_key (str):
        body (CreateModelVersionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | ModelVersion]
    """

    kwargs = _get_kwargs(
        model_key=model_key,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    model_key: str,
    *,
    client: AuthenticatedClient | Client,
    body: CreateModelVersionRequest,
) -> Error | ModelVersion | None:
    """Create a draft Model version

    Args:
        model_key (str):
        body (CreateModelVersionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | ModelVersion
    """

    return (
        await asyncio_detailed(
            model_key=model_key,
            client=client,
            body=body,
        )
    ).parsed
