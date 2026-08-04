from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.get_openapi_v1_streams_stream_id_experiments_response_200 import (
    GetOpenapiV1StreamsStreamIdExperimentsResponse200,
)
from ...types import Response


def _get_kwargs(
    stream_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/openapi/v1/streams/{stream_id}/experiments".format(
            stream_id=quote(str(stream_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | GetOpenapiV1StreamsStreamIdExperimentsResponse200:
    if response.status_code == 200:
        response_200 = GetOpenapiV1StreamsStreamIdExperimentsResponse200.from_dict(
            response.json()
        )

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | GetOpenapiV1StreamsStreamIdExperimentsResponse200]:
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
) -> Response[Error | GetOpenapiV1StreamsStreamIdExperimentsResponse200]:
    """List the experiment dimensions present in a Stream

     Buckets the Stream's tasks by experiment, newest first. The row whose experiment is empty holds the
    tasks launched without one, and the first row is the dimension the task and stats endpoints default
    to.

    Args:
        stream_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | GetOpenapiV1StreamsStreamIdExperimentsResponse200]
    """

    kwargs = _get_kwargs(
        stream_id=stream_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    stream_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Error | GetOpenapiV1StreamsStreamIdExperimentsResponse200 | None:
    """List the experiment dimensions present in a Stream

     Buckets the Stream's tasks by experiment, newest first. The row whose experiment is empty holds the
    tasks launched without one, and the first row is the dimension the task and stats endpoints default
    to.

    Args:
        stream_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | GetOpenapiV1StreamsStreamIdExperimentsResponse200
    """

    return sync_detailed(
        stream_id=stream_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    stream_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[Error | GetOpenapiV1StreamsStreamIdExperimentsResponse200]:
    """List the experiment dimensions present in a Stream

     Buckets the Stream's tasks by experiment, newest first. The row whose experiment is empty holds the
    tasks launched without one, and the first row is the dimension the task and stats endpoints default
    to.

    Args:
        stream_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | GetOpenapiV1StreamsStreamIdExperimentsResponse200]
    """

    kwargs = _get_kwargs(
        stream_id=stream_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    stream_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Error | GetOpenapiV1StreamsStreamIdExperimentsResponse200 | None:
    """List the experiment dimensions present in a Stream

     Buckets the Stream's tasks by experiment, newest first. The row whose experiment is empty holds the
    tasks launched without one, and the first row is the dimension the task and stats endpoints default
    to.

    Args:
        stream_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | GetOpenapiV1StreamsStreamIdExperimentsResponse200
    """

    return (
        await asyncio_detailed(
            stream_id=stream_id,
            client=client,
        )
    ).parsed
