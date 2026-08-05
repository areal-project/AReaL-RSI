from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.launch_batch_task_request import LaunchBatchTaskRequest
from ...models.launch_batch_task_response import LaunchBatchTaskResponse
from ...types import Response


def _get_kwargs(
    stream_id: str,
    *,
    body: LaunchBatchTaskRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/openapi/v1/streams/{stream_id}/launch_batch_task".format(
            stream_id=quote(str(stream_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | LaunchBatchTaskResponse:
    if response.status_code == 202:
        response_202 = LaunchBatchTaskResponse.from_dict(response.json())

        return response_202

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | LaunchBatchTaskResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    stream_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: LaunchBatchTaskRequest,
) -> Response[Error | LaunchBatchTaskResponse]:
    """Launch one Stream evaluation Job containing multiple Tasks

     Creates one Job and fans the requested data IDs into Tasks controlled by one concurrency policy. The
    model is resolved from the Model Registry; per-task Harness and Reward refs override the request
    defaults.

    Args:
        stream_id (str):
        body (LaunchBatchTaskRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | LaunchBatchTaskResponse]
    """

    kwargs = _get_kwargs(
        stream_id=stream_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    stream_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: LaunchBatchTaskRequest,
) -> Error | LaunchBatchTaskResponse | None:
    """Launch one Stream evaluation Job containing multiple Tasks

     Creates one Job and fans the requested data IDs into Tasks controlled by one concurrency policy. The
    model is resolved from the Model Registry; per-task Harness and Reward refs override the request
    defaults.

    Args:
        stream_id (str):
        body (LaunchBatchTaskRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | LaunchBatchTaskResponse
    """

    return sync_detailed(
        stream_id=stream_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    stream_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: LaunchBatchTaskRequest,
) -> Response[Error | LaunchBatchTaskResponse]:
    """Launch one Stream evaluation Job containing multiple Tasks

     Creates one Job and fans the requested data IDs into Tasks controlled by one concurrency policy. The
    model is resolved from the Model Registry; per-task Harness and Reward refs override the request
    defaults.

    Args:
        stream_id (str):
        body (LaunchBatchTaskRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | LaunchBatchTaskResponse]
    """

    kwargs = _get_kwargs(
        stream_id=stream_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    stream_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: LaunchBatchTaskRequest,
) -> Error | LaunchBatchTaskResponse | None:
    """Launch one Stream evaluation Job containing multiple Tasks

     Creates one Job and fans the requested data IDs into Tasks controlled by one concurrency policy. The
    model is resolved from the Model Registry; per-task Harness and Reward refs override the request
    defaults.

    Args:
        stream_id (str):
        body (LaunchBatchTaskRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | LaunchBatchTaskResponse
    """

    return (
        await asyncio_detailed(
            stream_id=stream_id,
            client=client,
            body=body,
        )
    ).parsed
