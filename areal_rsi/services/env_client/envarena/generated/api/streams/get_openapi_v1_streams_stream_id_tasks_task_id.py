from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.stream_task_row import StreamTaskRow
from ...types import Response


def _get_kwargs(
    stream_id: str,
    task_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/openapi/v1/streams/{stream_id}/tasks/{task_id}".format(
            stream_id=quote(str(stream_id), safe=""),
            task_id=quote(str(task_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | StreamTaskRow:
    if response.status_code == 200:
        response_200 = StreamTaskRow.from_dict(response.json())

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | StreamTaskRow]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    stream_id: str,
    task_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[Error | StreamTaskRow]:
    """Get one online Stream task row

    Args:
        stream_id (str):
        task_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | StreamTaskRow]
    """

    kwargs = _get_kwargs(
        stream_id=stream_id,
        task_id=task_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    stream_id: str,
    task_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Error | StreamTaskRow | None:
    """Get one online Stream task row

    Args:
        stream_id (str):
        task_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | StreamTaskRow
    """

    return sync_detailed(
        stream_id=stream_id,
        task_id=task_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    stream_id: str,
    task_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[Error | StreamTaskRow]:
    """Get one online Stream task row

    Args:
        stream_id (str):
        task_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | StreamTaskRow]
    """

    kwargs = _get_kwargs(
        stream_id=stream_id,
        task_id=task_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    stream_id: str,
    task_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Error | StreamTaskRow | None:
    """Get one online Stream task row

    Args:
        stream_id (str):
        task_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | StreamTaskRow
    """

    return (
        await asyncio_detailed(
            stream_id=stream_id,
            task_id=task_id,
            client=client,
        )
    ).parsed
