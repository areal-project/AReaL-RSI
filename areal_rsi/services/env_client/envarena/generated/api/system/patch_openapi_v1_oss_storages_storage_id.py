from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.oss_storage import OSSStorage
from ...models.oss_storage_request import OSSStorageRequest
from ...types import Response


def _get_kwargs(
    storage_id: str,
    *,
    body: OSSStorageRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "patch",
        "url": "/openapi/v1/oss-storages/{storage_id}".format(
            storage_id=quote(str(storage_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | OSSStorage:
    if response.status_code == 200:
        response_200 = OSSStorage.from_dict(response.json())

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | OSSStorage]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    storage_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: OSSStorageRequest,
) -> Response[Error | OSSStorage]:
    """Update an additional OSS backend (root only)

     Bucket, endpoint, prefix, and mount_id become immutable after any Registry version references the
    backend. Credentials may still be rotated.

    Args:
        storage_id (str):
        body (OSSStorageRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | OSSStorage]
    """

    kwargs = _get_kwargs(
        storage_id=storage_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    storage_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: OSSStorageRequest,
) -> Error | OSSStorage | None:
    """Update an additional OSS backend (root only)

     Bucket, endpoint, prefix, and mount_id become immutable after any Registry version references the
    backend. Credentials may still be rotated.

    Args:
        storage_id (str):
        body (OSSStorageRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | OSSStorage
    """

    return sync_detailed(
        storage_id=storage_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    storage_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: OSSStorageRequest,
) -> Response[Error | OSSStorage]:
    """Update an additional OSS backend (root only)

     Bucket, endpoint, prefix, and mount_id become immutable after any Registry version references the
    backend. Credentials may still be rotated.

    Args:
        storage_id (str):
        body (OSSStorageRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | OSSStorage]
    """

    kwargs = _get_kwargs(
        storage_id=storage_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    storage_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: OSSStorageRequest,
) -> Error | OSSStorage | None:
    """Update an additional OSS backend (root only)

     Bucket, endpoint, prefix, and mount_id become immutable after any Registry version references the
    backend. Credentials may still be rotated.

    Args:
        storage_id (str):
        body (OSSStorageRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | OSSStorage
    """

    return (
        await asyncio_detailed(
            storage_id=storage_id,
            client=client,
            body=body,
        )
    ).parsed
