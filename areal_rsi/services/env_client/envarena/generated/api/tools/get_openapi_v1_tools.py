from http import HTTPStatus
from typing import Any

import httpx

from ...client import AuthenticatedClient, Client
from ...models.entity_status import EntityStatus
from ...models.error import Error
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    status: EntityStatus | Unset = UNSET,
    tag: list[str] | Unset = UNSET,
    keys: list[str] | Unset = UNSET,
    created_by: str | Unset = UNSET,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_status: str | Unset = UNSET
    if not isinstance(status, Unset):
        json_status = status.value

    params["status"] = json_status

    json_tag: list[str] | Unset = UNSET
    if not isinstance(tag, Unset):
        json_tag = tag

    params["tag"] = json_tag

    json_keys: list[str] | Unset = UNSET
    if not isinstance(keys, Unset):
        json_keys = keys

    params["keys"] = json_keys

    params["created_by"] = created_by

    params["limit"] = limit

    params["offset"] = offset

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/openapi/v1/tools",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error:
    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    status: EntityStatus | Unset = UNSET,
    tag: list[str] | Unset = UNSET,
    keys: list[str] | Unset = UNSET,
    created_by: str | Unset = UNSET,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
) -> Response[Error]:
    """List Tool keys

    Args:
        status (EntityStatus | Unset):
        tag (list[str] | Unset):
        keys (list[str] | Unset):
        created_by (str | Unset):
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error]
    """

    kwargs = _get_kwargs(
        status=status,
        tag=tag,
        keys=keys,
        created_by=created_by,
        limit=limit,
        offset=offset,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    status: EntityStatus | Unset = UNSET,
    tag: list[str] | Unset = UNSET,
    keys: list[str] | Unset = UNSET,
    created_by: str | Unset = UNSET,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
) -> Error | None:
    """List Tool keys

    Args:
        status (EntityStatus | Unset):
        tag (list[str] | Unset):
        keys (list[str] | Unset):
        created_by (str | Unset):
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error
    """

    return sync_detailed(
        client=client,
        status=status,
        tag=tag,
        keys=keys,
        created_by=created_by,
        limit=limit,
        offset=offset,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    status: EntityStatus | Unset = UNSET,
    tag: list[str] | Unset = UNSET,
    keys: list[str] | Unset = UNSET,
    created_by: str | Unset = UNSET,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
) -> Response[Error]:
    """List Tool keys

    Args:
        status (EntityStatus | Unset):
        tag (list[str] | Unset):
        keys (list[str] | Unset):
        created_by (str | Unset):
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error]
    """

    kwargs = _get_kwargs(
        status=status,
        tag=tag,
        keys=keys,
        created_by=created_by,
        limit=limit,
        offset=offset,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    status: EntityStatus | Unset = UNSET,
    tag: list[str] | Unset = UNSET,
    keys: list[str] | Unset = UNSET,
    created_by: str | Unset = UNSET,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
) -> Error | None:
    """List Tool keys

    Args:
        status (EntityStatus | Unset):
        tag (list[str] | Unset):
        keys (list[str] | Unset):
        created_by (str | Unset):
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error
    """

    return (
        await asyncio_detailed(
            client=client,
            status=status,
            tag=tag,
            keys=keys,
            created_by=created_by,
            limit=limit,
            offset=offset,
        )
    ).parsed
