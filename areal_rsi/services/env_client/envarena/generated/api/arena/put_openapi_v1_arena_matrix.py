from http import HTTPStatus
from typing import Any

import httpx

from ...client import AuthenticatedClient, Client
from ...models.arena_matrix import ArenaMatrix
from ...models.error import Error
from ...types import Response


def _get_kwargs(
    *,
    body: ArenaMatrix,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "put",
        "url": "/openapi/v1/arena/matrix",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ArenaMatrix | Error:
    if response.status_code == 200:
        response_200 = ArenaMatrix.from_dict(response.json())

        return response_200

    if response.status_code == 403:
        response_403 = Error.from_dict(response.json())

        return response_403

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ArenaMatrix | Error]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: ArenaMatrix,
) -> Response[ArenaMatrix | Error]:
    """Replace the arena model-matrix dashboard config (root only)

     Overwrites the arena matrix. Requires the root identity. The server normalizes ids, dedupes
    benchmark refs / raw names / job ids, and coerces each issue status to WIP unless FIXED.

    Args:
        body (ArenaMatrix):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ArenaMatrix | Error]
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
    body: ArenaMatrix,
) -> ArenaMatrix | Error | None:
    """Replace the arena model-matrix dashboard config (root only)

     Overwrites the arena matrix. Requires the root identity. The server normalizes ids, dedupes
    benchmark refs / raw names / job ids, and coerces each issue status to WIP unless FIXED.

    Args:
        body (ArenaMatrix):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ArenaMatrix | Error
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: ArenaMatrix,
) -> Response[ArenaMatrix | Error]:
    """Replace the arena model-matrix dashboard config (root only)

     Overwrites the arena matrix. Requires the root identity. The server normalizes ids, dedupes
    benchmark refs / raw names / job ids, and coerces each issue status to WIP unless FIXED.

    Args:
        body (ArenaMatrix):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ArenaMatrix | Error]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: ArenaMatrix,
) -> ArenaMatrix | Error | None:
    """Replace the arena model-matrix dashboard config (root only)

     Overwrites the arena matrix. Requires the root identity. The server normalizes ids, dedupes
    benchmark refs / raw names / job ids, and coerces each issue status to WIP unless FIXED.

    Args:
        body (ArenaMatrix):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ArenaMatrix | Error
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
