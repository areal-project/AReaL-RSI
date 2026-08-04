from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.launch_one_task_response import LaunchOneTaskResponse
from ...models.play_forge_launch_response import PlayForgeLaunchResponse
from ...types import Response


def _get_kwargs(
    stream_id: str,
    *,
    body: Any,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/openapi/v1/streams/{stream_id}/launch_one_task".format(
            stream_id=quote(str(stream_id), safe=""),
        ),
    }

    _kwargs["json"] = body

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | LaunchOneTaskResponse | PlayForgeLaunchResponse:
    if response.status_code == 201:
        response_201 = PlayForgeLaunchResponse.from_dict(response.json())

        return response_201

    if response.status_code == 202:
        response_202 = LaunchOneTaskResponse.from_dict(response.json())

        return response_202

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | LaunchOneTaskResponse | PlayForgeLaunchResponse]:
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
    body: Any,
) -> Response[Error | LaunchOneTaskResponse | PlayForgeLaunchResponse]:
    """Launch one online task or PlayForge sandbox

     Standard online requests return 202 after scheduling. Requests with `playforge` synchronously create
    an ARCA sandbox, persist the derived Harness mount plan, and return a pre-signed port-8080 URL for
    the PlayForge OpenAI SSE data plane. The public wrapper does not accept caller-selected network,
    storage, or template fields.

    Args:
        stream_id (str):
        body (Any): Supply standard online-task fields, or a `playforge` object. A PlayForge
            request does not register a Harness and does not require `data_id`, `base_url`, or
            `api_key`.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | LaunchOneTaskResponse | PlayForgeLaunchResponse]
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
    body: Any,
) -> Error | LaunchOneTaskResponse | PlayForgeLaunchResponse | None:
    """Launch one online task or PlayForge sandbox

     Standard online requests return 202 after scheduling. Requests with `playforge` synchronously create
    an ARCA sandbox, persist the derived Harness mount plan, and return a pre-signed port-8080 URL for
    the PlayForge OpenAI SSE data plane. The public wrapper does not accept caller-selected network,
    storage, or template fields.

    Args:
        stream_id (str):
        body (Any): Supply standard online-task fields, or a `playforge` object. A PlayForge
            request does not register a Harness and does not require `data_id`, `base_url`, or
            `api_key`.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | LaunchOneTaskResponse | PlayForgeLaunchResponse
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
    body: Any,
) -> Response[Error | LaunchOneTaskResponse | PlayForgeLaunchResponse]:
    """Launch one online task or PlayForge sandbox

     Standard online requests return 202 after scheduling. Requests with `playforge` synchronously create
    an ARCA sandbox, persist the derived Harness mount plan, and return a pre-signed port-8080 URL for
    the PlayForge OpenAI SSE data plane. The public wrapper does not accept caller-selected network,
    storage, or template fields.

    Args:
        stream_id (str):
        body (Any): Supply standard online-task fields, or a `playforge` object. A PlayForge
            request does not register a Harness and does not require `data_id`, `base_url`, or
            `api_key`.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | LaunchOneTaskResponse | PlayForgeLaunchResponse]
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
    body: Any,
) -> Error | LaunchOneTaskResponse | PlayForgeLaunchResponse | None:
    """Launch one online task or PlayForge sandbox

     Standard online requests return 202 after scheduling. Requests with `playforge` synchronously create
    an ARCA sandbox, persist the derived Harness mount plan, and return a pre-signed port-8080 URL for
    the PlayForge OpenAI SSE data plane. The public wrapper does not accept caller-selected network,
    storage, or template fields.

    Args:
        stream_id (str):
        body (Any): Supply standard online-task fields, or a `playforge` object. A PlayForge
            request does not register a Harness and does not require `data_id`, `base_url`, or
            `api_key`.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | LaunchOneTaskResponse | PlayForgeLaunchResponse
    """

    return (
        await asyncio_detailed(
            stream_id=stream_id,
            client=client,
            body=body,
        )
    ).parsed
