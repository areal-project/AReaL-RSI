from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.get_openapi_v1_jobs_job_id_tasks_task_id_logs_kind_kind import (
    GetOpenapiV1JobsJobIdTasksTaskIdLogsKindKind,
)
from ...models.get_openapi_v1_jobs_job_id_tasks_task_id_logs_kind_response_200 import (
    GetOpenapiV1JobsJobIdTasksTaskIdLogsKindResponse200,
)
from ...types import UNSET, Response, Unset


def _get_kwargs(
    job_id: str,
    task_id: str,
    kind: GetOpenapiV1JobsJobIdTasksTaskIdLogsKindKind,
    *,
    attempt: int | Unset = UNSET,
    offset: int | Unset = UNSET,
    limit: int | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["attempt"] = attempt

    params["offset"] = offset

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/openapi/v1/jobs/{job_id}/tasks/{task_id}/logs/{kind}".format(
            job_id=quote(str(job_id), safe=""),
            task_id=quote(str(task_id), safe=""),
            kind=quote(str(kind), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | GetOpenapiV1JobsJobIdTasksTaskIdLogsKindResponse200:
    if response.status_code == 200:
        response_200 = GetOpenapiV1JobsJobIdTasksTaskIdLogsKindResponse200.from_dict(
            response.json()
        )

        return response_200

    if response.status_code == 400:
        response_400 = Error.from_dict(response.json())

        return response_400

    if response.status_code == 404:
        response_404 = Error.from_dict(response.json())

        return response_404

    if response.status_code == 502:
        response_502 = Error.from_dict(response.json())

        return response_502

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | GetOpenapiV1JobsJobIdTasksTaskIdLogsKindResponse200]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    job_id: str,
    task_id: str,
    kind: GetOpenapiV1JobsJobIdTasksTaskIdLogsKindKind,
    *,
    client: AuthenticatedClient | Client,
    attempt: int | Unset = UNSET,
    offset: int | Unset = UNSET,
    limit: int | Unset = UNSET,
) -> Response[Error | GetOpenapiV1JobsJobIdTasksTaskIdLogsKindResponse200]:
    """Read one page of a task execution log

     Returns a bounded, UTF-8-safe byte page of the harness, runner, or reward log stored for one task
    attempt. Page boundaries are aligned to UTF-8 rune starts, so the returned offset may be up to three
    bytes after the requested offset and content never splits a multi-byte character. kind=runner falls
    back to the harness log and kind=reward falls back to the shared-sandbox reward log when the
    dedicated object is absent; source_kind reports which object was served. Follow next_offset to read
    consecutive pages without gaps; next_offset is omitted on the final page.

    Args:
        job_id (str):
        task_id (str):
        kind (GetOpenapiV1JobsJobIdTasksTaskIdLogsKindKind):
        attempt (int | Unset):
        offset (int | Unset):
        limit (int | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | GetOpenapiV1JobsJobIdTasksTaskIdLogsKindResponse200]
    """

    kwargs = _get_kwargs(
        job_id=job_id,
        task_id=task_id,
        kind=kind,
        attempt=attempt,
        offset=offset,
        limit=limit,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    job_id: str,
    task_id: str,
    kind: GetOpenapiV1JobsJobIdTasksTaskIdLogsKindKind,
    *,
    client: AuthenticatedClient | Client,
    attempt: int | Unset = UNSET,
    offset: int | Unset = UNSET,
    limit: int | Unset = UNSET,
) -> Error | GetOpenapiV1JobsJobIdTasksTaskIdLogsKindResponse200 | None:
    """Read one page of a task execution log

     Returns a bounded, UTF-8-safe byte page of the harness, runner, or reward log stored for one task
    attempt. Page boundaries are aligned to UTF-8 rune starts, so the returned offset may be up to three
    bytes after the requested offset and content never splits a multi-byte character. kind=runner falls
    back to the harness log and kind=reward falls back to the shared-sandbox reward log when the
    dedicated object is absent; source_kind reports which object was served. Follow next_offset to read
    consecutive pages without gaps; next_offset is omitted on the final page.

    Args:
        job_id (str):
        task_id (str):
        kind (GetOpenapiV1JobsJobIdTasksTaskIdLogsKindKind):
        attempt (int | Unset):
        offset (int | Unset):
        limit (int | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | GetOpenapiV1JobsJobIdTasksTaskIdLogsKindResponse200
    """

    return sync_detailed(
        job_id=job_id,
        task_id=task_id,
        kind=kind,
        client=client,
        attempt=attempt,
        offset=offset,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    job_id: str,
    task_id: str,
    kind: GetOpenapiV1JobsJobIdTasksTaskIdLogsKindKind,
    *,
    client: AuthenticatedClient | Client,
    attempt: int | Unset = UNSET,
    offset: int | Unset = UNSET,
    limit: int | Unset = UNSET,
) -> Response[Error | GetOpenapiV1JobsJobIdTasksTaskIdLogsKindResponse200]:
    """Read one page of a task execution log

     Returns a bounded, UTF-8-safe byte page of the harness, runner, or reward log stored for one task
    attempt. Page boundaries are aligned to UTF-8 rune starts, so the returned offset may be up to three
    bytes after the requested offset and content never splits a multi-byte character. kind=runner falls
    back to the harness log and kind=reward falls back to the shared-sandbox reward log when the
    dedicated object is absent; source_kind reports which object was served. Follow next_offset to read
    consecutive pages without gaps; next_offset is omitted on the final page.

    Args:
        job_id (str):
        task_id (str):
        kind (GetOpenapiV1JobsJobIdTasksTaskIdLogsKindKind):
        attempt (int | Unset):
        offset (int | Unset):
        limit (int | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | GetOpenapiV1JobsJobIdTasksTaskIdLogsKindResponse200]
    """

    kwargs = _get_kwargs(
        job_id=job_id,
        task_id=task_id,
        kind=kind,
        attempt=attempt,
        offset=offset,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    job_id: str,
    task_id: str,
    kind: GetOpenapiV1JobsJobIdTasksTaskIdLogsKindKind,
    *,
    client: AuthenticatedClient | Client,
    attempt: int | Unset = UNSET,
    offset: int | Unset = UNSET,
    limit: int | Unset = UNSET,
) -> Error | GetOpenapiV1JobsJobIdTasksTaskIdLogsKindResponse200 | None:
    """Read one page of a task execution log

     Returns a bounded, UTF-8-safe byte page of the harness, runner, or reward log stored for one task
    attempt. Page boundaries are aligned to UTF-8 rune starts, so the returned offset may be up to three
    bytes after the requested offset and content never splits a multi-byte character. kind=runner falls
    back to the harness log and kind=reward falls back to the shared-sandbox reward log when the
    dedicated object is absent; source_kind reports which object was served. Follow next_offset to read
    consecutive pages without gaps; next_offset is omitted on the final page.

    Args:
        job_id (str):
        task_id (str):
        kind (GetOpenapiV1JobsJobIdTasksTaskIdLogsKindKind):
        attempt (int | Unset):
        offset (int | Unset):
        limit (int | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | GetOpenapiV1JobsJobIdTasksTaskIdLogsKindResponse200
    """

    return (
        await asyncio_detailed(
            job_id=job_id,
            task_id=task_id,
            kind=kind,
            client=client,
            attempt=attempt,
            offset=offset,
            limit=limit,
        )
    ).parsed
