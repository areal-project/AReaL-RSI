from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.get_openapi_v1_jobs_job_id_tasks_status import (
    GetOpenapiV1JobsJobIdTasksStatus,
)
from ...models.job_task_list_response import JobTaskListResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    job_id: str,
    *,
    env_key: str | Unset = UNSET,
    status: GetOpenapiV1JobsJobIdTasksStatus
    | Unset = GetOpenapiV1JobsJobIdTasksStatus.ALL,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["env_key"] = env_key

    json_status: str | Unset = UNSET
    if not isinstance(status, Unset):
        json_status = status.value

    params["status"] = json_status

    params["limit"] = limit

    params["offset"] = offset

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/openapi/v1/jobs/{job_id}/tasks".format(
            job_id=quote(str(job_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | JobTaskListResponse:
    if response.status_code == 200:
        response_200 = JobTaskListResponse.from_dict(response.json())

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | JobTaskListResponse]:
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
    env_key: str | Unset = UNSET,
    status: GetOpenapiV1JobsJobIdTasksStatus
    | Unset = GetOpenapiV1JobsJobIdTasksStatus.ALL,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
) -> Response[Error | JobTaskListResponse]:
    """List paged Tasks for a BenchmarkJob

    Args:
        job_id (str):
        env_key (str | Unset):
        status (GetOpenapiV1JobsJobIdTasksStatus | Unset):  Default:
            GetOpenapiV1JobsJobIdTasksStatus.ALL.
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | JobTaskListResponse]
    """

    kwargs = _get_kwargs(
        job_id=job_id,
        env_key=env_key,
        status=status,
        limit=limit,
        offset=offset,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    job_id: str,
    *,
    client: AuthenticatedClient | Client,
    env_key: str | Unset = UNSET,
    status: GetOpenapiV1JobsJobIdTasksStatus
    | Unset = GetOpenapiV1JobsJobIdTasksStatus.ALL,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
) -> Error | JobTaskListResponse | None:
    """List paged Tasks for a BenchmarkJob

    Args:
        job_id (str):
        env_key (str | Unset):
        status (GetOpenapiV1JobsJobIdTasksStatus | Unset):  Default:
            GetOpenapiV1JobsJobIdTasksStatus.ALL.
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | JobTaskListResponse
    """

    return sync_detailed(
        job_id=job_id,
        client=client,
        env_key=env_key,
        status=status,
        limit=limit,
        offset=offset,
    ).parsed


async def asyncio_detailed(
    job_id: str,
    *,
    client: AuthenticatedClient | Client,
    env_key: str | Unset = UNSET,
    status: GetOpenapiV1JobsJobIdTasksStatus
    | Unset = GetOpenapiV1JobsJobIdTasksStatus.ALL,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
) -> Response[Error | JobTaskListResponse]:
    """List paged Tasks for a BenchmarkJob

    Args:
        job_id (str):
        env_key (str | Unset):
        status (GetOpenapiV1JobsJobIdTasksStatus | Unset):  Default:
            GetOpenapiV1JobsJobIdTasksStatus.ALL.
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | JobTaskListResponse]
    """

    kwargs = _get_kwargs(
        job_id=job_id,
        env_key=env_key,
        status=status,
        limit=limit,
        offset=offset,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    job_id: str,
    *,
    client: AuthenticatedClient | Client,
    env_key: str | Unset = UNSET,
    status: GetOpenapiV1JobsJobIdTasksStatus
    | Unset = GetOpenapiV1JobsJobIdTasksStatus.ALL,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
) -> Error | JobTaskListResponse | None:
    """List paged Tasks for a BenchmarkJob

    Args:
        job_id (str):
        env_key (str | Unset):
        status (GetOpenapiV1JobsJobIdTasksStatus | Unset):  Default:
            GetOpenapiV1JobsJobIdTasksStatus.ALL.
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | JobTaskListResponse
    """

    return (
        await asyncio_detailed(
            job_id=job_id,
            client=client,
            env_key=env_key,
            status=status,
            limit=limit,
            offset=offset,
        )
    ).parsed
