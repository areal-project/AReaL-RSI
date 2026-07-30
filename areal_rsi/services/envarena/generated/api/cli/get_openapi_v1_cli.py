from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.cli_manifest import CLIManifest
from ...models.error import Error
from ...types import Response


def _get_kwargs() -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/openapi/v1/cli",
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> CLIManifest | Error | None:
    if response.status_code == 200:
        response_200 = CLIManifest.from_dict(response.json())

        return response_200

    if response.status_code == 404:
        response_404 = Error.from_dict(response.json())

        return response_404

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[CLIManifest | Error]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[CLIManifest | Error]:
    """Get the latest arena CLI release (version + download links)

     Public. Returns the latest published arena CLI release with per-platform download URLs that stream
    back through this API (gateway-reachable), so install.sh and `arena update` work before a token is
    configured.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[CLIManifest | Error]
    """

    kwargs = _get_kwargs()

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
) -> CLIManifest | Error | None:
    """Get the latest arena CLI release (version + download links)

     Public. Returns the latest published arena CLI release with per-platform download URLs that stream
    back through this API (gateway-reachable), so install.sh and `arena update` work before a token is
    configured.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        CLIManifest | Error
    """

    return sync_detailed(
        client=client,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[CLIManifest | Error]:
    """Get the latest arena CLI release (version + download links)

     Public. Returns the latest published arena CLI release with per-platform download URLs that stream
    back through this API (gateway-reachable), so install.sh and `arena update` work before a token is
    configured.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[CLIManifest | Error]
    """

    kwargs = _get_kwargs()

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
) -> CLIManifest | Error | None:
    """Get the latest arena CLI release (version + download links)

     Public. Returns the latest published arena CLI release with per-platform download URLs that stream
    back through this API (gateway-reachable), so install.sh and `arena update` work before a token is
    configured.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        CLIManifest | Error
    """

    return (
        await asyncio_detailed(
            client=client,
        )
    ).parsed
