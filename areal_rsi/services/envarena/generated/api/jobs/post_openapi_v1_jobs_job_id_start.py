from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.start_job_response import StartJobResponse
from ...types import Response


def _get_kwargs(
    job_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/openapi/v1/jobs/{job_id}/start".format(
            job_id=quote(str(job_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | StartJobResponse:
    if response.status_code == 200:
        response_200 = StartJobResponse.from_dict(response.json())

        return response_200

    if response.status_code == 409:
        response_409 = Error.from_dict(response.json())

        return response_409

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | StartJobResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    job_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[Error | StartJobResponse]:
    """Start (resume) a STOPPED or CANCELLED BenchmarkJob

     Resumes a STOPPED or CANCELLED job. Every non-DONE task is reset to
    PENDING and re-scheduled under the job's current concurrency/retry
    policy; its stale reward is deleted, along with the aggregate job
    result. DONE tasks keep their status, rewards and trajectories. Task
    attempt counters are not reset, so historical attempt artifacts are
    never overwritten.

    Args:
        job_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | StartJobResponse]
    """

    kwargs = _get_kwargs(
        job_id=job_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    job_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Error | StartJobResponse | None:
    """Start (resume) a STOPPED or CANCELLED BenchmarkJob

     Resumes a STOPPED or CANCELLED job. Every non-DONE task is reset to
    PENDING and re-scheduled under the job's current concurrency/retry
    policy; its stale reward is deleted, along with the aggregate job
    result. DONE tasks keep their status, rewards and trajectories. Task
    attempt counters are not reset, so historical attempt artifacts are
    never overwritten.

    Args:
        job_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | StartJobResponse
    """

    return sync_detailed(
        job_id=job_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    job_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[Error | StartJobResponse]:
    """Start (resume) a STOPPED or CANCELLED BenchmarkJob

     Resumes a STOPPED or CANCELLED job. Every non-DONE task is reset to
    PENDING and re-scheduled under the job's current concurrency/retry
    policy; its stale reward is deleted, along with the aggregate job
    result. DONE tasks keep their status, rewards and trajectories. Task
    attempt counters are not reset, so historical attempt artifacts are
    never overwritten.

    Args:
        job_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | StartJobResponse]
    """

    kwargs = _get_kwargs(
        job_id=job_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    job_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Error | StartJobResponse | None:
    """Start (resume) a STOPPED or CANCELLED BenchmarkJob

     Resumes a STOPPED or CANCELLED job. Every non-DONE task is reset to
    PENDING and re-scheduled under the job's current concurrency/retry
    policy; its stale reward is deleted, along with the aggregate job
    result. DONE tasks keep their status, rewards and trajectories. Task
    attempt counters are not reset, so historical attempt artifacts are
    never overwritten.

    Args:
        job_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | StartJobResponse
    """

    return (
        await asyncio_detailed(
            job_id=job_id,
            client=client,
        )
    ).parsed
