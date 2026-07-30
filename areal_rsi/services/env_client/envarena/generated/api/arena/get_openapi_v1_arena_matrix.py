from http import HTTPStatus
from typing import Any

import httpx

from ...client import AuthenticatedClient, Client
from ...models.arena_matrix import ArenaMatrix
from ...models.error import Error
from ...types import Response


def _get_kwargs() -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/openapi/v1/arena/matrix",
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ArenaMatrix | Error:
    if response.status_code == 200:
        response_200 = ArenaMatrix.from_dict(response.json())

        return response_200

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
) -> Response[ArenaMatrix | Error]:
    """Get the arena model-matrix dashboard config

     Returns the stored arena matrix (stages → ability rows → benchmark refs and tracked issues). Falls
    back to a built-in default when unset.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ArenaMatrix | Error]
    """

    kwargs = _get_kwargs()

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
) -> ArenaMatrix | Error | None:
    """Get the arena model-matrix dashboard config

     Returns the stored arena matrix (stages → ability rows → benchmark refs and tracked issues). Falls
    back to a built-in default when unset.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ArenaMatrix | Error
    """

    return sync_detailed(
        client=client,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[ArenaMatrix | Error]:
    """Get the arena model-matrix dashboard config

     Returns the stored arena matrix (stages → ability rows → benchmark refs and tracked issues). Falls
    back to a built-in default when unset.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ArenaMatrix | Error]
    """

    kwargs = _get_kwargs()

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
) -> ArenaMatrix | Error | None:
    """Get the arena model-matrix dashboard config

     Returns the stored arena matrix (stages → ability rows → benchmark refs and tracked issues). Falls
    back to a built-in default when unset.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ArenaMatrix | Error
    """

    return (
        await asyncio_detailed(
            client=client,
        )
    ).parsed
