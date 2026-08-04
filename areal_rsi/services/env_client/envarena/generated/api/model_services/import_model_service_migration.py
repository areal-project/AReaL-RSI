from http import HTTPStatus
from typing import Any, cast

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.import_model_service_migration_body import (
    ImportModelServiceMigrationBody,
)
from ...models.import_model_service_migration_response_200 import (
    ImportModelServiceMigrationResponse200,
)
from ...types import Response


def _get_kwargs(
    *,
    body: ImportModelServiceMigrationBody,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/openapi/v1/model-services/migrations",
    }

    _kwargs["files"] = body.to_multipart()

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | ImportModelServiceMigrationResponse200 | None:
    if response.status_code == 200:
        response_200 = ImportModelServiceMigrationResponse200.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = cast(Any, None)
        return response_400

    if response.status_code == 403:
        response_403 = cast(Any, None)
        return response_403

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Any | ImportModelServiceMigrationResponse200]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: ImportModelServiceMigrationBody,
) -> Response[Any | ImportModelServiceMigrationResponse200]:
    """Import an AModelService -> Arena model-service exchange file (root only)

    Args:
        body (ImportModelServiceMigrationBody):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | ImportModelServiceMigrationResponse200]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    body: ImportModelServiceMigrationBody,
) -> Any | ImportModelServiceMigrationResponse200 | None:
    """Import an AModelService -> Arena model-service exchange file (root only)

    Args:
        body (ImportModelServiceMigrationBody):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | ImportModelServiceMigrationResponse200
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: ImportModelServiceMigrationBody,
) -> Response[Any | ImportModelServiceMigrationResponse200]:
    """Import an AModelService -> Arena model-service exchange file (root only)

    Args:
        body (ImportModelServiceMigrationBody):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | ImportModelServiceMigrationResponse200]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: ImportModelServiceMigrationBody,
) -> Any | ImportModelServiceMigrationResponse200 | None:
    """Import an AModelService -> Arena model-service exchange file (root only)

    Args:
        body (ImportModelServiceMigrationBody):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | ImportModelServiceMigrationResponse200
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
