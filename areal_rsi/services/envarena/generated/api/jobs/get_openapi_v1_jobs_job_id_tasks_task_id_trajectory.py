from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.get_openapi_v1_jobs_job_id_tasks_task_id_trajectory_format import (
    GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryFormat,
)
from ...models.get_openapi_v1_jobs_job_id_tasks_task_id_trajectory_response_200 import (
    GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryResponse200,
)
from ...types import UNSET, Response, Unset


def _get_kwargs(
    job_id: str,
    task_id: str,
    *,
    attempt: int | Unset = UNSET,
    format_: GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryFormat | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["attempt"] = attempt

    json_format_: str | Unset = UNSET
    if not isinstance(format_, Unset):
        json_format_ = format_.value

    params["format"] = json_format_

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/openapi/v1/jobs/{job_id}/tasks/{task_id}/trajectory".format(
            job_id=quote(str(job_id), safe=""),
            task_id=quote(str(task_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryResponse200:
    if response.status_code == 200:
        response_200 = GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryResponse200.from_dict(
            response.json()
        )

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryResponse200]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    job_id: str,
    task_id: str,
    *,
    client: AuthenticatedClient | Client,
    attempt: int | Unset = UNSET,
    format_: GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryFormat | Unset = UNSET,
) -> Response[Error | GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryResponse200]:
    """Get the complete trajectory of a single task attempt

     Returns the full, structured stream-json trajectory for one task attempt. Default format=json parses
    each event into a JSON object array (no truncation, no escaping); format=raw streams the verbatim
    NDJSON. attempt defaults to the latest (task.attempts); valid range 1..attempts.

    Args:
        job_id (str):
        task_id (str):
        attempt (int | Unset):
        format_ (GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryFormat | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryResponse200]
    """

    kwargs = _get_kwargs(
        job_id=job_id,
        task_id=task_id,
        attempt=attempt,
        format_=format_,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    job_id: str,
    task_id: str,
    *,
    client: AuthenticatedClient | Client,
    attempt: int | Unset = UNSET,
    format_: GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryFormat | Unset = UNSET,
) -> Error | GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryResponse200 | None:
    """Get the complete trajectory of a single task attempt

     Returns the full, structured stream-json trajectory for one task attempt. Default format=json parses
    each event into a JSON object array (no truncation, no escaping); format=raw streams the verbatim
    NDJSON. attempt defaults to the latest (task.attempts); valid range 1..attempts.

    Args:
        job_id (str):
        task_id (str):
        attempt (int | Unset):
        format_ (GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryFormat | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryResponse200
    """

    return sync_detailed(
        job_id=job_id,
        task_id=task_id,
        client=client,
        attempt=attempt,
        format_=format_,
    ).parsed


async def asyncio_detailed(
    job_id: str,
    task_id: str,
    *,
    client: AuthenticatedClient | Client,
    attempt: int | Unset = UNSET,
    format_: GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryFormat | Unset = UNSET,
) -> Response[Error | GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryResponse200]:
    """Get the complete trajectory of a single task attempt

     Returns the full, structured stream-json trajectory for one task attempt. Default format=json parses
    each event into a JSON object array (no truncation, no escaping); format=raw streams the verbatim
    NDJSON. attempt defaults to the latest (task.attempts); valid range 1..attempts.

    Args:
        job_id (str):
        task_id (str):
        attempt (int | Unset):
        format_ (GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryFormat | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryResponse200]
    """

    kwargs = _get_kwargs(
        job_id=job_id,
        task_id=task_id,
        attempt=attempt,
        format_=format_,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    job_id: str,
    task_id: str,
    *,
    client: AuthenticatedClient | Client,
    attempt: int | Unset = UNSET,
    format_: GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryFormat | Unset = UNSET,
) -> Error | GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryResponse200 | None:
    """Get the complete trajectory of a single task attempt

     Returns the full, structured stream-json trajectory for one task attempt. Default format=json parses
    each event into a JSON object array (no truncation, no escaping); format=raw streams the verbatim
    NDJSON. attempt defaults to the latest (task.attempts); valid range 1..attempts.

    Args:
        job_id (str):
        task_id (str):
        attempt (int | Unset):
        format_ (GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryFormat | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryResponse200
    """

    return (
        await asyncio_detailed(
            job_id=job_id,
            task_id=task_id,
            client=client,
            attempt=attempt,
            format_=format_,
        )
    ).parsed
