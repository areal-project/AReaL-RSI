from http import HTTPStatus
from typing import Any

import httpx

from ...client import AuthenticatedClient, Client
from ...models.entity_status import EntityStatus
from ...models.error import Error
from ...models.registry_version_list_response import RegistryVersionListResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    status: EntityStatus | Unset = UNSET,
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
        "url": "/openapi/v1/benchmarks/versions",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | RegistryVersionListResponse:
    if response.status_code == 200:
        response_200 = RegistryVersionListResponse.from_dict(response.json())

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | RegistryVersionListResponse]:
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
    keys: list[str] | Unset = UNSET,
    created_by: str | Unset = UNSET,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
) -> Response[Error | RegistryVersionListResponse]:
    """List Benchmark versions across entity keys

    Args:
        status (EntityStatus | Unset):
        keys (list[str] | Unset):
        created_by (str | Unset):
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | RegistryVersionListResponse]
    """

    kwargs = _get_kwargs(
        status=status,
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
    keys: list[str] | Unset = UNSET,
    created_by: str | Unset = UNSET,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
) -> Error | RegistryVersionListResponse | None:
    """List Benchmark versions across entity keys

    Args:
        status (EntityStatus | Unset):
        keys (list[str] | Unset):
        created_by (str | Unset):
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | RegistryVersionListResponse
    """

    return sync_detailed(
        client=client,
        status=status,
        keys=keys,
        created_by=created_by,
        limit=limit,
        offset=offset,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    status: EntityStatus | Unset = UNSET,
    keys: list[str] | Unset = UNSET,
    created_by: str | Unset = UNSET,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
) -> Response[Error | RegistryVersionListResponse]:
    """List Benchmark versions across entity keys

    Args:
        status (EntityStatus | Unset):
        keys (list[str] | Unset):
        created_by (str | Unset):
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | RegistryVersionListResponse]
    """

    kwargs = _get_kwargs(
        status=status,
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
    keys: list[str] | Unset = UNSET,
    created_by: str | Unset = UNSET,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
) -> Error | RegistryVersionListResponse | None:
    """List Benchmark versions across entity keys

    Args:
        status (EntityStatus | Unset):
        keys (list[str] | Unset):
        created_by (str | Unset):
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | RegistryVersionListResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            status=status,
            keys=keys,
            created_by=created_by,
            limit=limit,
            offset=offset,
        )
    ).parsed
