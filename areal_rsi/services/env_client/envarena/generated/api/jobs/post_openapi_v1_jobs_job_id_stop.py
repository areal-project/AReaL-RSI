from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.stop_job_response import StopJobResponse
from ...types import Response


def _get_kwargs(
    job_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/openapi/v1/jobs/{job_id}/stop".format(
            job_id=quote(str(job_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | StopJobResponse:
    if response.status_code == 200:
        response_200 = StopJobResponse.from_dict(response.json())

        return response_200

    if response.status_code == 409:
        response_409 = Error.from_dict(response.json())

        return response_409

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | StopJobResponse]:
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
) -> Response[Error | StopJobResponse]:
    """Stop a BenchmarkJob (resumable pause)

     Drains the job into the resumable STOPPED state: scheduling is
    cancelled, active sandboxes are destroyed and non-terminal tasks are
    parked as CANCELLED with their sandbox ids cleared. Unlike cancel —
    which abandons the job for good — a stopped job can be resumed via
    start; DONE tasks and their rewards are preserved. Allowed from
    PENDING/RUNNING/AGGREGATING/FAILED/TIMEOUT; re-stopping a
    STOPPING/STOPPED job is an idempotent convergence.

    Args:
        job_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | StopJobResponse]
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
) -> Error | StopJobResponse | None:
    """Stop a BenchmarkJob (resumable pause)

     Drains the job into the resumable STOPPED state: scheduling is
    cancelled, active sandboxes are destroyed and non-terminal tasks are
    parked as CANCELLED with their sandbox ids cleared. Unlike cancel —
    which abandons the job for good — a stopped job can be resumed via
    start; DONE tasks and their rewards are preserved. Allowed from
    PENDING/RUNNING/AGGREGATING/FAILED/TIMEOUT; re-stopping a
    STOPPING/STOPPED job is an idempotent convergence.

    Args:
        job_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | StopJobResponse
    """

    return sync_detailed(
        job_id=job_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    job_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[Error | StopJobResponse]:
    """Stop a BenchmarkJob (resumable pause)

     Drains the job into the resumable STOPPED state: scheduling is
    cancelled, active sandboxes are destroyed and non-terminal tasks are
    parked as CANCELLED with their sandbox ids cleared. Unlike cancel —
    which abandons the job for good — a stopped job can be resumed via
    start; DONE tasks and their rewards are preserved. Allowed from
    PENDING/RUNNING/AGGREGATING/FAILED/TIMEOUT; re-stopping a
    STOPPING/STOPPED job is an idempotent convergence.

    Args:
        job_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | StopJobResponse]
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
) -> Error | StopJobResponse | None:
    """Stop a BenchmarkJob (resumable pause)

     Drains the job into the resumable STOPPED state: scheduling is
    cancelled, active sandboxes are destroyed and non-terminal tasks are
    parked as CANCELLED with their sandbox ids cleared. Unlike cancel —
    which abandons the job for good — a stopped job can be resumed via
    start; DONE tasks and their rewards are preserved. Allowed from
    PENDING/RUNNING/AGGREGATING/FAILED/TIMEOUT; re-stopping a
    STOPPING/STOPPED job is an idempotent convergence.

    Args:
        job_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | StopJobResponse
    """

    return (
        await asyncio_detailed(
            job_id=job_id,
            client=client,
        )
    ).parsed
