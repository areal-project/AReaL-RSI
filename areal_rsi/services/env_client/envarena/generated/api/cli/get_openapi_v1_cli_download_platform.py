from http import HTTPStatus
from io import BytesIO
from typing import Any, cast
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...types import File, Response


def _get_kwargs(
    platform: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/openapi/v1/cli/download/{platform}".format(
            platform=quote(str(platform), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | Error | File | None:
    if response.status_code == 200:
        response_200 = File(payload=BytesIO(response.content))

        return response_200

    if response.status_code == 302:
        response_302 = cast(Any, None)
        return response_302

    if response.status_code == 404:
        response_404 = Error.from_dict(response.json())

        return response_404

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Any | Error | File]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    platform: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[Any | Error | File]:
    """Download the arena CLI binary for a platform

     Public. Streams the platform binary from OSS (or 302-redirects when the release references an
    absolute URL). platform is a GOOS-GOARCH token, e.g. darwin-arm64 or linux-amd64.

    Args:
        platform (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | Error | File]
    """

    kwargs = _get_kwargs(
        platform=platform,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    platform: str,
    *,
    client: AuthenticatedClient | Client,
) -> Any | Error | File | None:
    """Download the arena CLI binary for a platform

     Public. Streams the platform binary from OSS (or 302-redirects when the release references an
    absolute URL). platform is a GOOS-GOARCH token, e.g. darwin-arm64 or linux-amd64.

    Args:
        platform (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | Error | File
    """

    return sync_detailed(
        platform=platform,
        client=client,
    ).parsed


async def asyncio_detailed(
    platform: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[Any | Error | File]:
    """Download the arena CLI binary for a platform

     Public. Streams the platform binary from OSS (or 302-redirects when the release references an
    absolute URL). platform is a GOOS-GOARCH token, e.g. darwin-arm64 or linux-amd64.

    Args:
        platform (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | Error | File]
    """

    kwargs = _get_kwargs(
        platform=platform,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    platform: str,
    *,
    client: AuthenticatedClient | Client,
) -> Any | Error | File | None:
    """Download the arena CLI binary for a platform

     Public. Streams the platform binary from OSS (or 302-redirects when the release references an
    absolute URL). platform is a GOOS-GOARCH token, e.g. darwin-arm64 or linux-amd64.

    Args:
        platform (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | Error | File
    """

    return (
        await asyncio_detailed(
            platform=platform,
            client=client,
        )
    ).parsed
