from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.retry_job_request import RetryJobRequest
from ...models.retry_job_response import RetryJobResponse
from ...types import Response


def _get_kwargs(
    job_id: str,
    *,
    body: RetryJobRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/openapi/v1/jobs/{job_id}/retry".format(
            job_id=quote(str(job_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | RetryJobResponse:
    if response.status_code == 200:
        response_200 = RetryJobResponse.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = Error.from_dict(response.json())

        return response_400

    if response.status_code == 409:
        response_409 = Error.from_dict(response.json())

        return response_409

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | RetryJobResponse]:
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
    body: RetryJobRequest,
) -> Response[Error | RetryJobResponse]:
    """Manually retry tasks of a settled job (failed / all / selected)

     Opens a new execution generation and re-runs the selected tasks.
    mode=failed re-runs failed tasks (FAILED/TIMEOUT/EVAL_FAILED/
    HARNESS_FAILED/SETUP_FAILED/COLLECT_FAILED/NO_OUTPUT — CANCELLED is
    excluded), mode=all re-runs every task, mode=selected re-runs the
    given task_ids. Task IDs and the global attempt counters are
    preserved; the previous generation's tasks, the rewards of the reset
    tasks and the job result are archived into the execution history
    first. Allowed only for settled jobs (DONE/FAILED/TIMEOUT/CANCELLED/
    STOPPED) whose previous run has released its lease. Supplying the
    same idempotency_key again returns the recorded outcome (reused=true)
    instead of opening another generation.

    Args:
        job_id (str):
        body (RetryJobRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | RetryJobResponse]
    """

    kwargs = _get_kwargs(
        job_id=job_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    job_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: RetryJobRequest,
) -> Error | RetryJobResponse | None:
    """Manually retry tasks of a settled job (failed / all / selected)

     Opens a new execution generation and re-runs the selected tasks.
    mode=failed re-runs failed tasks (FAILED/TIMEOUT/EVAL_FAILED/
    HARNESS_FAILED/SETUP_FAILED/COLLECT_FAILED/NO_OUTPUT — CANCELLED is
    excluded), mode=all re-runs every task, mode=selected re-runs the
    given task_ids. Task IDs and the global attempt counters are
    preserved; the previous generation's tasks, the rewards of the reset
    tasks and the job result are archived into the execution history
    first. Allowed only for settled jobs (DONE/FAILED/TIMEOUT/CANCELLED/
    STOPPED) whose previous run has released its lease. Supplying the
    same idempotency_key again returns the recorded outcome (reused=true)
    instead of opening another generation.

    Args:
        job_id (str):
        body (RetryJobRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | RetryJobResponse
    """

    return sync_detailed(
        job_id=job_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    job_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: RetryJobRequest,
) -> Response[Error | RetryJobResponse]:
    """Manually retry tasks of a settled job (failed / all / selected)

     Opens a new execution generation and re-runs the selected tasks.
    mode=failed re-runs failed tasks (FAILED/TIMEOUT/EVAL_FAILED/
    HARNESS_FAILED/SETUP_FAILED/COLLECT_FAILED/NO_OUTPUT — CANCELLED is
    excluded), mode=all re-runs every task, mode=selected re-runs the
    given task_ids. Task IDs and the global attempt counters are
    preserved; the previous generation's tasks, the rewards of the reset
    tasks and the job result are archived into the execution history
    first. Allowed only for settled jobs (DONE/FAILED/TIMEOUT/CANCELLED/
    STOPPED) whose previous run has released its lease. Supplying the
    same idempotency_key again returns the recorded outcome (reused=true)
    instead of opening another generation.

    Args:
        job_id (str):
        body (RetryJobRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | RetryJobResponse]
    """

    kwargs = _get_kwargs(
        job_id=job_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    job_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: RetryJobRequest,
) -> Error | RetryJobResponse | None:
    """Manually retry tasks of a settled job (failed / all / selected)

     Opens a new execution generation and re-runs the selected tasks.
    mode=failed re-runs failed tasks (FAILED/TIMEOUT/EVAL_FAILED/
    HARNESS_FAILED/SETUP_FAILED/COLLECT_FAILED/NO_OUTPUT — CANCELLED is
    excluded), mode=all re-runs every task, mode=selected re-runs the
    given task_ids. Task IDs and the global attempt counters are
    preserved; the previous generation's tasks, the rewards of the reset
    tasks and the job result are archived into the execution history
    first. Allowed only for settled jobs (DONE/FAILED/TIMEOUT/CANCELLED/
    STOPPED) whose previous run has released its lease. Supplying the
    same idempotency_key again returns the recorded outcome (reused=true)
    instead of opening another generation.

    Args:
        job_id (str):
        body (RetryJobRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | RetryJobResponse
    """

    return (
        await asyncio_detailed(
            job_id=job_id,
            client=client,
            body=body,
        )
    ).parsed
