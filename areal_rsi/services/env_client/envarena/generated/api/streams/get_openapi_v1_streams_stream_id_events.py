from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.get_openapi_v1_streams_stream_id_events_response_200 import (
    GetOpenapiV1StreamsStreamIdEventsResponse200,
)
from ...types import UNSET, Response, Unset


def _get_kwargs(
    stream_id: str,
    *,
    limit: int | Unset = 100,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/openapi/v1/streams/{stream_id}/events".format(
            stream_id=quote(str(stream_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | GetOpenapiV1StreamsStreamIdEventsResponse200:
    if response.status_code == 200:
        response_200 = GetOpenapiV1StreamsStreamIdEventsResponse200.from_dict(
            response.json()
        )

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | GetOpenapiV1StreamsStreamIdEventsResponse200]:
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
    limit: int | Unset = 100,
) -> Response[Error | GetOpenapiV1StreamsStreamIdEventsResponse200]:
    """List Stream configuration change events

     Contains Stream create, update, archive, and delete changes only; task lifecycle events are
    intentionally excluded.

    Args:
        stream_id (str):
        limit (int | Unset):  Default: 100.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | GetOpenapiV1StreamsStreamIdEventsResponse200]
    """

    kwargs = _get_kwargs(
        stream_id=stream_id,
        limit=limit,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    stream_id: str,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 100,
) -> Error | GetOpenapiV1StreamsStreamIdEventsResponse200 | None:
    """List Stream configuration change events

     Contains Stream create, update, archive, and delete changes only; task lifecycle events are
    intentionally excluded.

    Args:
        stream_id (str):
        limit (int | Unset):  Default: 100.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | GetOpenapiV1StreamsStreamIdEventsResponse200
    """

    return sync_detailed(
        stream_id=stream_id,
        client=client,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    stream_id: str,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 100,
) -> Response[Error | GetOpenapiV1StreamsStreamIdEventsResponse200]:
    """List Stream configuration change events

     Contains Stream create, update, archive, and delete changes only; task lifecycle events are
    intentionally excluded.

    Args:
        stream_id (str):
        limit (int | Unset):  Default: 100.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | GetOpenapiV1StreamsStreamIdEventsResponse200]
    """

    kwargs = _get_kwargs(
        stream_id=stream_id,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    stream_id: str,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 100,
) -> Error | GetOpenapiV1StreamsStreamIdEventsResponse200 | None:
    """List Stream configuration change events

     Contains Stream create, update, archive, and delete changes only; task lifecycle events are
    intentionally excluded.

    Args:
        stream_id (str):
        limit (int | Unset):  Default: 100.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | GetOpenapiV1StreamsStreamIdEventsResponse200
    """

    return (
        await asyncio_detailed(
            stream_id=stream_id,
            client=client,
            limit=limit,
        )
    ).parsed
