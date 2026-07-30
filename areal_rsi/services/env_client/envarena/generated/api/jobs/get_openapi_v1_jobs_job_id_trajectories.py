from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.get_openapi_v1_jobs_job_id_trajectories_response_200 import (
    GetOpenapiV1JobsJobIdTrajectoriesResponse200,
)
from ...types import UNSET, Response, Unset


def _get_kwargs(
    job_id: str,
    *,
    inline: bool | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["inline"] = inline

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/openapi/v1/jobs/{job_id}/trajectories".format(
            job_id=quote(str(job_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | GetOpenapiV1JobsJobIdTrajectoriesResponse200:
    if response.status_code == 200:
        response_200 = GetOpenapiV1JobsJobIdTrajectoriesResponse200.from_dict(
            response.json()
        )

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | GetOpenapiV1JobsJobIdTrajectoriesResponse200]:
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
    inline: bool | Unset = UNSET,
) -> Response[Error | GetOpenapiV1JobsJobIdTrajectoriesResponse200]:
    """List trajectory references for every task in a job

     Returns one entry per task with a reference to its trajectory (trajectory_uri + metadata). Pass
    inline=true to embed the parsed events of each task's latest attempt.

    Args:
        job_id (str):
        inline (bool | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | GetOpenapiV1JobsJobIdTrajectoriesResponse200]
    """

    kwargs = _get_kwargs(
        job_id=job_id,
        inline=inline,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    job_id: str,
    *,
    client: AuthenticatedClient | Client,
    inline: bool | Unset = UNSET,
) -> Error | GetOpenapiV1JobsJobIdTrajectoriesResponse200 | None:
    """List trajectory references for every task in a job

     Returns one entry per task with a reference to its trajectory (trajectory_uri + metadata). Pass
    inline=true to embed the parsed events of each task's latest attempt.

    Args:
        job_id (str):
        inline (bool | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | GetOpenapiV1JobsJobIdTrajectoriesResponse200
    """

    return sync_detailed(
        job_id=job_id,
        client=client,
        inline=inline,
    ).parsed


async def asyncio_detailed(
    job_id: str,
    *,
    client: AuthenticatedClient | Client,
    inline: bool | Unset = UNSET,
) -> Response[Error | GetOpenapiV1JobsJobIdTrajectoriesResponse200]:
    """List trajectory references for every task in a job

     Returns one entry per task with a reference to its trajectory (trajectory_uri + metadata). Pass
    inline=true to embed the parsed events of each task's latest attempt.

    Args:
        job_id (str):
        inline (bool | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | GetOpenapiV1JobsJobIdTrajectoriesResponse200]
    """

    kwargs = _get_kwargs(
        job_id=job_id,
        inline=inline,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    job_id: str,
    *,
    client: AuthenticatedClient | Client,
    inline: bool | Unset = UNSET,
) -> Error | GetOpenapiV1JobsJobIdTrajectoriesResponse200 | None:
    """List trajectory references for every task in a job

     Returns one entry per task with a reference to its trajectory (trajectory_uri + metadata). Pass
    inline=true to embed the parsed events of each task's latest attempt.

    Args:
        job_id (str):
        inline (bool | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | GetOpenapiV1JobsJobIdTrajectoriesResponse200
    """

    return (
        await asyncio_detailed(
            job_id=job_id,
            client=client,
            inline=inline,
        )
    ).parsed
