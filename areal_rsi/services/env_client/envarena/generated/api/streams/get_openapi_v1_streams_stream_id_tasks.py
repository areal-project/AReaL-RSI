from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.get_openapi_v1_streams_stream_id_tasks_response_200 import (
    GetOpenapiV1StreamsStreamIdTasksResponse200,
)
from ...types import UNSET, Response, Unset


def _get_kwargs(
    stream_id: str,
    *,
    status: str | Unset = UNSET,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["status"] = status

    params["limit"] = limit

    params["offset"] = offset

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/openapi/v1/streams/{stream_id}/tasks".format(
            stream_id=quote(str(stream_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | GetOpenapiV1StreamsStreamIdTasksResponse200:
    if response.status_code == 200:
        response_200 = GetOpenapiV1StreamsStreamIdTasksResponse200.from_dict(
            response.json()
        )

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | GetOpenapiV1StreamsStreamIdTasksResponse200]:
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
    status: str | Unset = UNSET,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
) -> Response[Error | GetOpenapiV1StreamsStreamIdTasksResponse200]:
    """List online tasks in a Stream

     Returns a database-paged task list; use limit and offset rather than loading every task in the
    Stream.

    Args:
        stream_id (str):
        status (str | Unset):
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | GetOpenapiV1StreamsStreamIdTasksResponse200]
    """

    kwargs = _get_kwargs(
        stream_id=stream_id,
        status=status,
        limit=limit,
        offset=offset,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    stream_id: str,
    *,
    client: AuthenticatedClient | Client,
    status: str | Unset = UNSET,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
) -> Error | GetOpenapiV1StreamsStreamIdTasksResponse200 | None:
    """List online tasks in a Stream

     Returns a database-paged task list; use limit and offset rather than loading every task in the
    Stream.

    Args:
        stream_id (str):
        status (str | Unset):
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | GetOpenapiV1StreamsStreamIdTasksResponse200
    """

    return sync_detailed(
        stream_id=stream_id,
        client=client,
        status=status,
        limit=limit,
        offset=offset,
    ).parsed


async def asyncio_detailed(
    stream_id: str,
    *,
    client: AuthenticatedClient | Client,
    status: str | Unset = UNSET,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
) -> Response[Error | GetOpenapiV1StreamsStreamIdTasksResponse200]:
    """List online tasks in a Stream

     Returns a database-paged task list; use limit and offset rather than loading every task in the
    Stream.

    Args:
        stream_id (str):
        status (str | Unset):
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | GetOpenapiV1StreamsStreamIdTasksResponse200]
    """

    kwargs = _get_kwargs(
        stream_id=stream_id,
        status=status,
        limit=limit,
        offset=offset,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    stream_id: str,
    *,
    client: AuthenticatedClient | Client,
    status: str | Unset = UNSET,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
) -> Error | GetOpenapiV1StreamsStreamIdTasksResponse200 | None:
    """List online tasks in a Stream

     Returns a database-paged task list; use limit and offset rather than loading every task in the
    Stream.

    Args:
        stream_id (str):
        status (str | Unset):
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | GetOpenapiV1StreamsStreamIdTasksResponse200
    """

    return (
        await asyncio_detailed(
            stream_id=stream_id,
            client=client,
            status=status,
            limit=limit,
            offset=offset,
        )
    ).parsed
