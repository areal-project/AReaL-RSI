from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...types import Response


def _get_kwargs(
    benchmark_key: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "delete",
        "url": "/openapi/v1/benchmarks/{benchmark_key}".format(
            benchmark_key=quote(str(benchmark_key), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | Error:
    if response.status_code == 204:
        response_204 = cast(Any, None)
        return response_204

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Any | Error]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    benchmark_key: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[Any | Error]:
    """Soft-delete a Benchmark key

     Marks the Benchmark as DELETED; normal reads hide it. Only root or the creator may delete it.

    Args:
        benchmark_key (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | Error]
    """

    kwargs = _get_kwargs(
        benchmark_key=benchmark_key,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    benchmark_key: str,
    *,
    client: AuthenticatedClient | Client,
) -> Any | Error | None:
    """Soft-delete a Benchmark key

     Marks the Benchmark as DELETED; normal reads hide it. Only root or the creator may delete it.

    Args:
        benchmark_key (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | Error
    """

    return sync_detailed(
        benchmark_key=benchmark_key,
        client=client,
    ).parsed


async def asyncio_detailed(
    benchmark_key: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[Any | Error]:
    """Soft-delete a Benchmark key

     Marks the Benchmark as DELETED; normal reads hide it. Only root or the creator may delete it.

    Args:
        benchmark_key (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | Error]
    """

    kwargs = _get_kwargs(
        benchmark_key=benchmark_key,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    benchmark_key: str,
    *,
    client: AuthenticatedClient | Client,
) -> Any | Error | None:
    """Soft-delete a Benchmark key

     Marks the Benchmark as DELETED; normal reads hide it. Only root or the creator may delete it.

    Args:
        benchmark_key (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | Error
    """

    return (
        await asyncio_detailed(
            benchmark_key=benchmark_key,
            client=client,
        )
    ).parsed
