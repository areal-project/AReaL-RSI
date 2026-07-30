from http import HTTPStatus
from typing import Any

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.get_openapi_v1_jobs_response_200 import GetOpenapiV1JobsResponse200
from ...models.job_status import JobStatus
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    status: JobStatus | Unset = UNSET,
    benchmark_key: str | Unset = UNSET,
    harness_key: str | Unset = UNSET,
    model_name: str | Unset = UNSET,
    tag: str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_status: str | Unset = UNSET
    if not isinstance(status, Unset):
        json_status = status.value

    params["status"] = json_status

    params["benchmark_key"] = benchmark_key

    params["harness_key"] = harness_key

    params["model_name"] = model_name

    params["tag"] = tag

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/openapi/v1/jobs",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | GetOpenapiV1JobsResponse200:
    if response.status_code == 200:
        response_200 = GetOpenapiV1JobsResponse200.from_dict(response.json())

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | GetOpenapiV1JobsResponse200]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    status: JobStatus | Unset = UNSET,
    benchmark_key: str | Unset = UNSET,
    harness_key: str | Unset = UNSET,
    model_name: str | Unset = UNSET,
    tag: str | Unset = UNSET,
) -> Response[Error | GetOpenapiV1JobsResponse200]:
    """List BenchmarkJobs

    Args:
        status (JobStatus | Unset):
        benchmark_key (str | Unset):
        harness_key (str | Unset):
        model_name (str | Unset):
        tag (str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | GetOpenapiV1JobsResponse200]
    """

    kwargs = _get_kwargs(
        status=status,
        benchmark_key=benchmark_key,
        harness_key=harness_key,
        model_name=model_name,
        tag=tag,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    status: JobStatus | Unset = UNSET,
    benchmark_key: str | Unset = UNSET,
    harness_key: str | Unset = UNSET,
    model_name: str | Unset = UNSET,
    tag: str | Unset = UNSET,
) -> Error | GetOpenapiV1JobsResponse200 | None:
    """List BenchmarkJobs

    Args:
        status (JobStatus | Unset):
        benchmark_key (str | Unset):
        harness_key (str | Unset):
        model_name (str | Unset):
        tag (str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | GetOpenapiV1JobsResponse200
    """

    return sync_detailed(
        client=client,
        status=status,
        benchmark_key=benchmark_key,
        harness_key=harness_key,
        model_name=model_name,
        tag=tag,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    status: JobStatus | Unset = UNSET,
    benchmark_key: str | Unset = UNSET,
    harness_key: str | Unset = UNSET,
    model_name: str | Unset = UNSET,
    tag: str | Unset = UNSET,
) -> Response[Error | GetOpenapiV1JobsResponse200]:
    """List BenchmarkJobs

    Args:
        status (JobStatus | Unset):
        benchmark_key (str | Unset):
        harness_key (str | Unset):
        model_name (str | Unset):
        tag (str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | GetOpenapiV1JobsResponse200]
    """

    kwargs = _get_kwargs(
        status=status,
        benchmark_key=benchmark_key,
        harness_key=harness_key,
        model_name=model_name,
        tag=tag,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    status: JobStatus | Unset = UNSET,
    benchmark_key: str | Unset = UNSET,
    harness_key: str | Unset = UNSET,
    model_name: str | Unset = UNSET,
    tag: str | Unset = UNSET,
) -> Error | GetOpenapiV1JobsResponse200 | None:
    """List BenchmarkJobs

    Args:
        status (JobStatus | Unset):
        benchmark_key (str | Unset):
        harness_key (str | Unset):
        model_name (str | Unset):
        tag (str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | GetOpenapiV1JobsResponse200
    """

    return (
        await asyncio_detailed(
            client=client,
            status=status,
            benchmark_key=benchmark_key,
            harness_key=harness_key,
            model_name=model_name,
            tag=tag,
        )
    ).parsed
