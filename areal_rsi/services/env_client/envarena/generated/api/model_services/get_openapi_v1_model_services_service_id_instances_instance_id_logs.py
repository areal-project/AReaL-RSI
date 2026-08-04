from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.model_service_instance_logs import ModelServiceInstanceLogs
from ...types import UNSET, Response, Unset


def _get_kwargs(
    service_id: str,
    instance_id: str,
    *,
    file_name: str | Unset = UNSET,
    tail_lines: int | Unset = 200,
    max_bytes: int | Unset = 200000,
    line_offset: int | Unset = UNSET,
    line_limit: int | Unset = 500,
    include_content: bool | Unset = True,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["file_name"] = file_name

    params["tail_lines"] = tail_lines

    params["max_bytes"] = max_bytes

    params["line_offset"] = line_offset

    params["line_limit"] = line_limit

    params["include_content"] = include_content

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/openapi/v1/model-services/{service_id}/instances/{instance_id}/logs".format(
            service_id=quote(str(service_id), safe=""),
            instance_id=quote(str(instance_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | ModelServiceInstanceLogs:
    if response.status_code == 200:
        response_200 = ModelServiceInstanceLogs.from_dict(response.json())

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | ModelServiceInstanceLogs]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    service_id: str,
    instance_id: str,
    *,
    client: AuthenticatedClient | Client,
    file_name: str | Unset = UNSET,
    tail_lines: int | Unset = 200,
    max_bytes: int | Unset = 200000,
    line_offset: int | Unset = UNSET,
    line_limit: int | Unset = 500,
    include_content: bool | Unset = True,
) -> Response[Error | ModelServiceInstanceLogs]:
    """Query local SGLang logs for one model service instance

    Args:
        service_id (str):
        instance_id (str):
        file_name (str | Unset):
        tail_lines (int | Unset):  Default: 200.
        max_bytes (int | Unset):  Default: 200000.
        line_offset (int | Unset):
        line_limit (int | Unset):  Default: 500.
        include_content (bool | Unset):  Default: True.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | ModelServiceInstanceLogs]
    """

    kwargs = _get_kwargs(
        service_id=service_id,
        instance_id=instance_id,
        file_name=file_name,
        tail_lines=tail_lines,
        max_bytes=max_bytes,
        line_offset=line_offset,
        line_limit=line_limit,
        include_content=include_content,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    service_id: str,
    instance_id: str,
    *,
    client: AuthenticatedClient | Client,
    file_name: str | Unset = UNSET,
    tail_lines: int | Unset = 200,
    max_bytes: int | Unset = 200000,
    line_offset: int | Unset = UNSET,
    line_limit: int | Unset = 500,
    include_content: bool | Unset = True,
) -> Error | ModelServiceInstanceLogs | None:
    """Query local SGLang logs for one model service instance

    Args:
        service_id (str):
        instance_id (str):
        file_name (str | Unset):
        tail_lines (int | Unset):  Default: 200.
        max_bytes (int | Unset):  Default: 200000.
        line_offset (int | Unset):
        line_limit (int | Unset):  Default: 500.
        include_content (bool | Unset):  Default: True.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | ModelServiceInstanceLogs
    """

    return sync_detailed(
        service_id=service_id,
        instance_id=instance_id,
        client=client,
        file_name=file_name,
        tail_lines=tail_lines,
        max_bytes=max_bytes,
        line_offset=line_offset,
        line_limit=line_limit,
        include_content=include_content,
    ).parsed


async def asyncio_detailed(
    service_id: str,
    instance_id: str,
    *,
    client: AuthenticatedClient | Client,
    file_name: str | Unset = UNSET,
    tail_lines: int | Unset = 200,
    max_bytes: int | Unset = 200000,
    line_offset: int | Unset = UNSET,
    line_limit: int | Unset = 500,
    include_content: bool | Unset = True,
) -> Response[Error | ModelServiceInstanceLogs]:
    """Query local SGLang logs for one model service instance

    Args:
        service_id (str):
        instance_id (str):
        file_name (str | Unset):
        tail_lines (int | Unset):  Default: 200.
        max_bytes (int | Unset):  Default: 200000.
        line_offset (int | Unset):
        line_limit (int | Unset):  Default: 500.
        include_content (bool | Unset):  Default: True.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | ModelServiceInstanceLogs]
    """

    kwargs = _get_kwargs(
        service_id=service_id,
        instance_id=instance_id,
        file_name=file_name,
        tail_lines=tail_lines,
        max_bytes=max_bytes,
        line_offset=line_offset,
        line_limit=line_limit,
        include_content=include_content,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    service_id: str,
    instance_id: str,
    *,
    client: AuthenticatedClient | Client,
    file_name: str | Unset = UNSET,
    tail_lines: int | Unset = 200,
    max_bytes: int | Unset = 200000,
    line_offset: int | Unset = UNSET,
    line_limit: int | Unset = 500,
    include_content: bool | Unset = True,
) -> Error | ModelServiceInstanceLogs | None:
    """Query local SGLang logs for one model service instance

    Args:
        service_id (str):
        instance_id (str):
        file_name (str | Unset):
        tail_lines (int | Unset):  Default: 200.
        max_bytes (int | Unset):  Default: 200000.
        line_offset (int | Unset):
        line_limit (int | Unset):  Default: 500.
        include_content (bool | Unset):  Default: True.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | ModelServiceInstanceLogs
    """

    return (
        await asyncio_detailed(
            service_id=service_id,
            instance_id=instance_id,
            client=client,
            file_name=file_name,
            tail_lines=tail_lines,
            max_bytes=max_bytes,
            line_offset=line_offset,
            line_limit=line_limit,
            include_content=include_content,
        )
    ).parsed
