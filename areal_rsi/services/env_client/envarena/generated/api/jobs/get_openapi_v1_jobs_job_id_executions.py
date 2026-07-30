from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.job_execution_list_response import JobExecutionListResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    job_id: str,
    *,
    full: bool | Unset = False,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["full"] = full

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/openapi/v1/jobs/{job_id}/executions".format(
            job_id=quote(str(job_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | JobExecutionListResponse:
    if response.status_code == 200:
        response_200 = JobExecutionListResponse.from_dict(response.json())

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | JobExecutionListResponse]:
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
    full: bool | Unset = False,
) -> Response[Error | JobExecutionListResponse]:
    """List the archived execution generations (manual retry history) of a job

     Returns one entry per archived generation, oldest first. Each entry
    summarizes the retry (mode, requester, reset task ids, snapshot
    counts, archived job result). Pass full=true to embed the full task
    and reward snapshots.

    Args:
        job_id (str):
        full (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | JobExecutionListResponse]
    """

    kwargs = _get_kwargs(
        job_id=job_id,
        full=full,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    job_id: str,
    *,
    client: AuthenticatedClient | Client,
    full: bool | Unset = False,
) -> Error | JobExecutionListResponse | None:
    """List the archived execution generations (manual retry history) of a job

     Returns one entry per archived generation, oldest first. Each entry
    summarizes the retry (mode, requester, reset task ids, snapshot
    counts, archived job result). Pass full=true to embed the full task
    and reward snapshots.

    Args:
        job_id (str):
        full (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | JobExecutionListResponse
    """

    return sync_detailed(
        job_id=job_id,
        client=client,
        full=full,
    ).parsed


async def asyncio_detailed(
    job_id: str,
    *,
    client: AuthenticatedClient | Client,
    full: bool | Unset = False,
) -> Response[Error | JobExecutionListResponse]:
    """List the archived execution generations (manual retry history) of a job

     Returns one entry per archived generation, oldest first. Each entry
    summarizes the retry (mode, requester, reset task ids, snapshot
    counts, archived job result). Pass full=true to embed the full task
    and reward snapshots.

    Args:
        job_id (str):
        full (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | JobExecutionListResponse]
    """

    kwargs = _get_kwargs(
        job_id=job_id,
        full=full,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    job_id: str,
    *,
    client: AuthenticatedClient | Client,
    full: bool | Unset = False,
) -> Error | JobExecutionListResponse | None:
    """List the archived execution generations (manual retry history) of a job

     Returns one entry per archived generation, oldest first. Each entry
    summarizes the retry (mode, requester, reset task ids, snapshot
    counts, archived job result). Pass full=true to embed the full task
    and reward snapshots.

    Args:
        job_id (str):
        full (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | JobExecutionListResponse
    """

    return (
        await asyncio_detailed(
            job_id=job_id,
            client=client,
            full=full,
        )
    ).parsed
