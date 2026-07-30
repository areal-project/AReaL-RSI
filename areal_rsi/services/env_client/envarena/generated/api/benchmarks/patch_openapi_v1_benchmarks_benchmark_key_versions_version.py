from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.benchmark_version import BenchmarkVersion
from ...models.error import Error
from ...models.update_benchmark_version_request import UpdateBenchmarkVersionRequest
from ...types import Response


def _get_kwargs(
    benchmark_key: str,
    version: str,
    *,
    body: UpdateBenchmarkVersionRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "patch",
        "url": "/openapi/v1/benchmarks/{benchmark_key}/versions/{version}".format(
            benchmark_key=quote(str(benchmark_key), safe=""),
            version=quote(str(version), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> BenchmarkVersion | Error:
    if response.status_code == 200:
        response_200 = BenchmarkVersion.from_dict(response.json())

        return response_200

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
    version: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateBenchmarkVersionRequest,
) -> Response[BenchmarkVersion | Error]:
    """Update a draft Benchmark version

    Args:
        benchmark_key (str):
        version (str):  Example: 1.0.0.
        body (UpdateBenchmarkVersionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[BenchmarkVersion | Error]
    """

    kwargs = _get_kwargs(
        benchmark_key=benchmark_key,
        version=version,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    benchmark_key: str,
    version: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateBenchmarkVersionRequest,
) -> BenchmarkVersion | Error | None:
    """Update a draft Benchmark version

    Args:
        benchmark_key (str):
        version (str):  Example: 1.0.0.
        body (UpdateBenchmarkVersionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        BenchmarkVersion | Error
    """

    return sync_detailed(
        benchmark_key=benchmark_key,
        version=version,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    benchmark_key: str,
    version: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateBenchmarkVersionRequest,
) -> Response[BenchmarkVersion | Error]:
    """Update a draft Benchmark version

    Args:
        benchmark_key (str):
        version (str):  Example: 1.0.0.
        body (UpdateBenchmarkVersionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[BenchmarkVersion | Error]
    """

    kwargs = _get_kwargs(
        benchmark_key=benchmark_key,
        version=version,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    benchmark_key: str,
    version: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateBenchmarkVersionRequest,
) -> BenchmarkVersion | Error | None:
    """Update a draft Benchmark version

    Args:
        benchmark_key (str):
        version (str):  Example: 1.0.0.
        body (UpdateBenchmarkVersionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        BenchmarkVersion | Error
    """

    return (
        await asyncio_detailed(
            benchmark_key=benchmark_key,
            version=version,
            client=client,
            body=body,
        )
    ).parsed
