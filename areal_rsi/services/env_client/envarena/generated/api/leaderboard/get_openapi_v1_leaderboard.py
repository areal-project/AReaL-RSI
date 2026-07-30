from http import HTTPStatus
from typing import Any

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.get_openapi_v1_leaderboard_response_200 import (
    GetOpenapiV1LeaderboardResponse200,
)
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    benchmark_key: str,
    benchmark_version: str | Unset = UNSET,
    harness_key: str | Unset = UNSET,
    model_name: str | Unset = UNSET,
    sample_numbers: int | Unset = UNSET,
    limit: int | Unset = 100,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["benchmark_key"] = benchmark_key

    params["benchmark_version"] = benchmark_version

    params["harness_key"] = harness_key

    params["model_name"] = model_name

    params["sample_numbers"] = sample_numbers

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/openapi/v1/leaderboard",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | GetOpenapiV1LeaderboardResponse200:
    if response.status_code == 200:
        response_200 = GetOpenapiV1LeaderboardResponse200.from_dict(response.json())

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | GetOpenapiV1LeaderboardResponse200]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    benchmark_key: str,
    benchmark_version: str | Unset = UNSET,
    harness_key: str | Unset = UNSET,
    model_name: str | Unset = UNSET,
    sample_numbers: int | Unset = UNSET,
    limit: int | Unset = 100,
) -> Response[Error | GetOpenapiV1LeaderboardResponse200]:
    """List ranked JobResult leaderboard rows for one Benchmark

    Args:
        benchmark_key (str):
        benchmark_version (str | Unset):
        harness_key (str | Unset):
        model_name (str | Unset):
        sample_numbers (int | Unset):
        limit (int | Unset):  Default: 100.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | GetOpenapiV1LeaderboardResponse200]
    """

    kwargs = _get_kwargs(
        benchmark_key=benchmark_key,
        benchmark_version=benchmark_version,
        harness_key=harness_key,
        model_name=model_name,
        sample_numbers=sample_numbers,
        limit=limit,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    benchmark_key: str,
    benchmark_version: str | Unset = UNSET,
    harness_key: str | Unset = UNSET,
    model_name: str | Unset = UNSET,
    sample_numbers: int | Unset = UNSET,
    limit: int | Unset = 100,
) -> Error | GetOpenapiV1LeaderboardResponse200 | None:
    """List ranked JobResult leaderboard rows for one Benchmark

    Args:
        benchmark_key (str):
        benchmark_version (str | Unset):
        harness_key (str | Unset):
        model_name (str | Unset):
        sample_numbers (int | Unset):
        limit (int | Unset):  Default: 100.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | GetOpenapiV1LeaderboardResponse200
    """

    return sync_detailed(
        client=client,
        benchmark_key=benchmark_key,
        benchmark_version=benchmark_version,
        harness_key=harness_key,
        model_name=model_name,
        sample_numbers=sample_numbers,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    benchmark_key: str,
    benchmark_version: str | Unset = UNSET,
    harness_key: str | Unset = UNSET,
    model_name: str | Unset = UNSET,
    sample_numbers: int | Unset = UNSET,
    limit: int | Unset = 100,
) -> Response[Error | GetOpenapiV1LeaderboardResponse200]:
    """List ranked JobResult leaderboard rows for one Benchmark

    Args:
        benchmark_key (str):
        benchmark_version (str | Unset):
        harness_key (str | Unset):
        model_name (str | Unset):
        sample_numbers (int | Unset):
        limit (int | Unset):  Default: 100.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | GetOpenapiV1LeaderboardResponse200]
    """

    kwargs = _get_kwargs(
        benchmark_key=benchmark_key,
        benchmark_version=benchmark_version,
        harness_key=harness_key,
        model_name=model_name,
        sample_numbers=sample_numbers,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    benchmark_key: str,
    benchmark_version: str | Unset = UNSET,
    harness_key: str | Unset = UNSET,
    model_name: str | Unset = UNSET,
    sample_numbers: int | Unset = UNSET,
    limit: int | Unset = 100,
) -> Error | GetOpenapiV1LeaderboardResponse200 | None:
    """List ranked JobResult leaderboard rows for one Benchmark

    Args:
        benchmark_key (str):
        benchmark_version (str | Unset):
        harness_key (str | Unset):
        model_name (str | Unset):
        sample_numbers (int | Unset):
        limit (int | Unset):  Default: 100.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | GetOpenapiV1LeaderboardResponse200
    """

    return (
        await asyncio_detailed(
            client=client,
            benchmark_key=benchmark_key,
            benchmark_version=benchmark_version,
            harness_key=harness_key,
            model_name=model_name,
            sample_numbers=sample_numbers,
            limit=limit,
        )
    ).parsed
