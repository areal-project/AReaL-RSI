from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.model_service_ais_job import ModelServiceAISJob
from ...types import UNSET, Response, Unset


def _get_kwargs(
    service_id: str,
    instance_id: str,
    *,
    include_details: bool | Unset = True,
    include_logs: bool | Unset = False,
    log_offset: int | Unset = 0,
    log_limit: int | Unset = 100,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["include_details"] = include_details

    params["include_logs"] = include_logs

    params["log_offset"] = log_offset

    params["log_limit"] = log_limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/openapi/v1/model-services/{service_id}/instances/{instance_id}/ais".format(
            service_id=quote(str(service_id), safe=""),
            instance_id=quote(str(instance_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | ModelServiceAISJob:
    if response.status_code == 200:
        response_200 = ModelServiceAISJob.from_dict(response.json())

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | ModelServiceAISJob]:
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
    include_details: bool | Unset = True,
    include_logs: bool | Unset = False,
    log_offset: int | Unset = 0,
    log_limit: int | Unset = 100,
) -> Response[Error | ModelServiceAISJob]:
    """Query AIS job detail for one model service instance

    Args:
        service_id (str):
        instance_id (str):
        include_details (bool | Unset):  Default: True.
        include_logs (bool | Unset):  Default: False.
        log_offset (int | Unset):  Default: 0.
        log_limit (int | Unset):  Default: 100.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | ModelServiceAISJob]
    """

    kwargs = _get_kwargs(
        service_id=service_id,
        instance_id=instance_id,
        include_details=include_details,
        include_logs=include_logs,
        log_offset=log_offset,
        log_limit=log_limit,
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
    include_details: bool | Unset = True,
    include_logs: bool | Unset = False,
    log_offset: int | Unset = 0,
    log_limit: int | Unset = 100,
) -> Error | ModelServiceAISJob | None:
    """Query AIS job detail for one model service instance

    Args:
        service_id (str):
        instance_id (str):
        include_details (bool | Unset):  Default: True.
        include_logs (bool | Unset):  Default: False.
        log_offset (int | Unset):  Default: 0.
        log_limit (int | Unset):  Default: 100.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | ModelServiceAISJob
    """

    return sync_detailed(
        service_id=service_id,
        instance_id=instance_id,
        client=client,
        include_details=include_details,
        include_logs=include_logs,
        log_offset=log_offset,
        log_limit=log_limit,
    ).parsed


async def asyncio_detailed(
    service_id: str,
    instance_id: str,
    *,
    client: AuthenticatedClient | Client,
    include_details: bool | Unset = True,
    include_logs: bool | Unset = False,
    log_offset: int | Unset = 0,
    log_limit: int | Unset = 100,
) -> Response[Error | ModelServiceAISJob]:
    """Query AIS job detail for one model service instance

    Args:
        service_id (str):
        instance_id (str):
        include_details (bool | Unset):  Default: True.
        include_logs (bool | Unset):  Default: False.
        log_offset (int | Unset):  Default: 0.
        log_limit (int | Unset):  Default: 100.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | ModelServiceAISJob]
    """

    kwargs = _get_kwargs(
        service_id=service_id,
        instance_id=instance_id,
        include_details=include_details,
        include_logs=include_logs,
        log_offset=log_offset,
        log_limit=log_limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    service_id: str,
    instance_id: str,
    *,
    client: AuthenticatedClient | Client,
    include_details: bool | Unset = True,
    include_logs: bool | Unset = False,
    log_offset: int | Unset = 0,
    log_limit: int | Unset = 100,
) -> Error | ModelServiceAISJob | None:
    """Query AIS job detail for one model service instance

    Args:
        service_id (str):
        instance_id (str):
        include_details (bool | Unset):  Default: True.
        include_logs (bool | Unset):  Default: False.
        log_offset (int | Unset):  Default: 0.
        log_limit (int | Unset):  Default: 100.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | ModelServiceAISJob
    """

    return (
        await asyncio_detailed(
            service_id=service_id,
            instance_id=instance_id,
            client=client,
            include_details=include_details,
            include_logs=include_logs,
            log_offset=log_offset,
            log_limit=log_limit,
        )
    ).parsed
