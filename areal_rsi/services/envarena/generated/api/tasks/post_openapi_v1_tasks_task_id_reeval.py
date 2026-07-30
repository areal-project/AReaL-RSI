from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.post_openapi_v1_tasks_task_id_reeval_body import (
    PostOpenapiV1TasksTaskIdReevalBody,
)
from ...models.task import Task
from ...types import UNSET, Response, Unset


def _get_kwargs(
    task_id: str,
    *,
    body: PostOpenapiV1TasksTaskIdReevalBody | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/openapi/v1/tasks/{task_id}/reeval".format(
            task_id=quote(str(task_id), safe=""),
        ),
    }

    if not isinstance(body, Unset):
        _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | Task:
    if response.status_code == 202:
        response_202 = Task.from_dict(response.json())

        return response_202

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | Task]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    task_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: PostOpenapiV1TasksTaskIdReevalBody | Unset = UNSET,
) -> Response[Error | Task]:
    """Re-run Reward for an existing Task artifact set and refresh JobResult

     Optional reward_ref must point to a published Reward version and pass the same oracle-schema
    compatibility checks used by Benchmark publish.

    Args:
        task_id (str):
        body (PostOpenapiV1TasksTaskIdReevalBody | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | Task]
    """

    kwargs = _get_kwargs(
        task_id=task_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    task_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: PostOpenapiV1TasksTaskIdReevalBody | Unset = UNSET,
) -> Error | Task | None:
    """Re-run Reward for an existing Task artifact set and refresh JobResult

     Optional reward_ref must point to a published Reward version and pass the same oracle-schema
    compatibility checks used by Benchmark publish.

    Args:
        task_id (str):
        body (PostOpenapiV1TasksTaskIdReevalBody | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | Task
    """

    return sync_detailed(
        task_id=task_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    task_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: PostOpenapiV1TasksTaskIdReevalBody | Unset = UNSET,
) -> Response[Error | Task]:
    """Re-run Reward for an existing Task artifact set and refresh JobResult

     Optional reward_ref must point to a published Reward version and pass the same oracle-schema
    compatibility checks used by Benchmark publish.

    Args:
        task_id (str):
        body (PostOpenapiV1TasksTaskIdReevalBody | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | Task]
    """

    kwargs = _get_kwargs(
        task_id=task_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    task_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: PostOpenapiV1TasksTaskIdReevalBody | Unset = UNSET,
) -> Error | Task | None:
    """Re-run Reward for an existing Task artifact set and refresh JobResult

     Optional reward_ref must point to a published Reward version and pass the same oracle-schema
    compatibility checks used by Benchmark publish.

    Args:
        task_id (str):
        body (PostOpenapiV1TasksTaskIdReevalBody | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | Task
    """

    return (
        await asyncio_detailed(
            task_id=task_id,
            client=client,
            body=body,
        )
    ).parsed
