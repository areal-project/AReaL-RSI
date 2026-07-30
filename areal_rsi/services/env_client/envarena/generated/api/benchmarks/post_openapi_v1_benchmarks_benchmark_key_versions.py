from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.benchmark_version import BenchmarkVersion
from ...models.create_benchmark_version_request import CreateBenchmarkVersionRequest
from ...models.error import Error
from ...types import Response


def _get_kwargs(
    benchmark_key: str,
    *,
    body: CreateBenchmarkVersionRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/openapi/v1/benchmarks/{benchmark_key}/versions".format(
            benchmark_key=quote(str(benchmark_key), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> BenchmarkVersion | Error:
    if response.status_code == 201:
        response_201 = BenchmarkVersion.from_dict(response.json())

        return response_201

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[BenchmarkVersion | Error]:
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
    body: CreateBenchmarkVersionRequest,
) -> Response[BenchmarkVersion | Error]:
    """Create a draft Benchmark version

    Args:
        benchmark_key (str):
        body (CreateBenchmarkVersionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[BenchmarkVersion | Error]
    """

    kwargs = _get_kwargs(
        benchmark_key=benchmark_key,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    benchmark_key: str,
    *,
    client: AuthenticatedClient | Client,
    body: CreateBenchmarkVersionRequest,
) -> BenchmarkVersion | Error | None:
    """Create a draft Benchmark version

    Args:
        benchmark_key (str):
        body (CreateBenchmarkVersionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        BenchmarkVersion | Error
    """

    return sync_detailed(
        benchmark_key=benchmark_key,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    benchmark_key: str,
    *,
    client: AuthenticatedClient | Client,
    body: CreateBenchmarkVersionRequest,
) -> Response[BenchmarkVersion | Error]:
    """Create a draft Benchmark version

    Args:
        benchmark_key (str):
        body (CreateBenchmarkVersionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[BenchmarkVersion | Error]
    """

    kwargs = _get_kwargs(
        benchmark_key=benchmark_key,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    benchmark_key: str,
    *,
    client: AuthenticatedClient | Client,
    body: CreateBenchmarkVersionRequest,
) -> BenchmarkVersion | Error | None:
    """Create a draft Benchmark version

    Args:
        benchmark_key (str):
        body (CreateBenchmarkVersionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        BenchmarkVersion | Error
    """

    return (
        await asyncio_detailed(
            benchmark_key=benchmark_key,
            client=client,
            body=body,
        )
    ).parsed
