from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.stream import Stream
from ...models.update_stream_request import UpdateStreamRequest
from ...types import Response


def _get_kwargs(
    stream_id: str,
    *,
    body: UpdateStreamRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "patch",
        "url": "/openapi/v1/streams/{stream_id}".format(
            stream_id=quote(str(stream_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | Stream:
    if response.status_code == 200:
        response_200 = Stream.from_dict(response.json())

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | Stream]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    stream_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateStreamRequest,
) -> Response[Error | Stream]:
    """Update mutable Stream configuration

    Args:
        stream_id (str):
        body (UpdateStreamRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | Stream]
    """

    kwargs = _get_kwargs(
        stream_id=stream_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    stream_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateStreamRequest,
) -> Error | Stream | None:
    """Update mutable Stream configuration

    Args:
        stream_id (str):
        body (UpdateStreamRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | Stream
    """

    return sync_detailed(
        stream_id=stream_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    stream_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateStreamRequest,
) -> Response[Error | Stream]:
    """Update mutable Stream configuration

    Args:
        stream_id (str):
        body (UpdateStreamRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | Stream]
    """

    kwargs = _get_kwargs(
        stream_id=stream_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    stream_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateStreamRequest,
) -> Error | Stream | None:
    """Update mutable Stream configuration

    Args:
        stream_id (str):
        body (UpdateStreamRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | Stream
    """

    return (
        await asyncio_detailed(
            stream_id=stream_id,
            client=client,
            body=body,
        )
    ).parsed
