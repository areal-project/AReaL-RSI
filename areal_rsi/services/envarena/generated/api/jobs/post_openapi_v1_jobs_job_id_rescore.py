from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.create_rescore_job_request import CreateRescoreJobRequest
from ...models.create_rescore_job_response import CreateRescoreJobResponse
from ...models.error import Error
from ...types import Response


def _get_kwargs(
    job_id: str,
    *,
    body: CreateRescoreJobRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/openapi/v1/jobs/{job_id}/rescore".format(
            job_id=quote(str(job_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> CreateRescoreJobResponse | Error:
    if response.status_code == 202:
        response_202 = CreateRescoreJobResponse.from_dict(response.json())

        return response_202

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
) -> Response[CreateRescoreJobResponse | Error]:
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
    body: CreateRescoreJobRequest,
) -> Response[CreateRescoreJobResponse | Error]:
    """Derive a standalone Job that re-scores completed Task products with another Reward

     Creates a RESCORE Job from a completed Benchmark Job. The source
    Task's workspace/ and out/ prefixes are mounted read-only into the
    new Reward sandbox; no Agent or Harness sandbox is started and no OSS
    artifact is copied. The derived Task owns a separate writable result/
    prefix. The source Job stays unchanged and cannot be manually retried
    while an active derived Job is mounting its products. Only PUBLISHED,
    oracle-compatible Rewards with runtime.kind other than shared_env are
    accepted. RESCORE Jobs are independent audit records and are excluded
    from the default leaderboard.

    Args:
        job_id (str):
        body (CreateRescoreJobRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[CreateRescoreJobResponse | Error]
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
    body: CreateRescoreJobRequest,
) -> CreateRescoreJobResponse | Error | None:
    """Derive a standalone Job that re-scores completed Task products with another Reward

     Creates a RESCORE Job from a completed Benchmark Job. The source
    Task's workspace/ and out/ prefixes are mounted read-only into the
    new Reward sandbox; no Agent or Harness sandbox is started and no OSS
    artifact is copied. The derived Task owns a separate writable result/
    prefix. The source Job stays unchanged and cannot be manually retried
    while an active derived Job is mounting its products. Only PUBLISHED,
    oracle-compatible Rewards with runtime.kind other than shared_env are
    accepted. RESCORE Jobs are independent audit records and are excluded
    from the default leaderboard.

    Args:
        job_id (str):
        body (CreateRescoreJobRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        CreateRescoreJobResponse | Error
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
    body: CreateRescoreJobRequest,
) -> Response[CreateRescoreJobResponse | Error]:
    """Derive a standalone Job that re-scores completed Task products with another Reward

     Creates a RESCORE Job from a completed Benchmark Job. The source
    Task's workspace/ and out/ prefixes are mounted read-only into the
    new Reward sandbox; no Agent or Harness sandbox is started and no OSS
    artifact is copied. The derived Task owns a separate writable result/
    prefix. The source Job stays unchanged and cannot be manually retried
    while an active derived Job is mounting its products. Only PUBLISHED,
    oracle-compatible Rewards with runtime.kind other than shared_env are
    accepted. RESCORE Jobs are independent audit records and are excluded
    from the default leaderboard.

    Args:
        job_id (str):
        body (CreateRescoreJobRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[CreateRescoreJobResponse | Error]
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
    body: CreateRescoreJobRequest,
) -> CreateRescoreJobResponse | Error | None:
    """Derive a standalone Job that re-scores completed Task products with another Reward

     Creates a RESCORE Job from a completed Benchmark Job. The source
    Task's workspace/ and out/ prefixes are mounted read-only into the
    new Reward sandbox; no Agent or Harness sandbox is started and no OSS
    artifact is copied. The derived Task owns a separate writable result/
    prefix. The source Job stays unchanged and cannot be manually retried
    while an active derived Job is mounting its products. Only PUBLISHED,
    oracle-compatible Rewards with runtime.kind other than shared_env are
    accepted. RESCORE Jobs are independent audit records and are excluded
    from the default leaderboard.

    Args:
        job_id (str):
        body (CreateRescoreJobRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        CreateRescoreJobResponse | Error
    """

    return (
        await asyncio_detailed(
            job_id=job_id,
            client=client,
            body=body,
        )
    ).parsed
