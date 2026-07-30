from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.cli_manifest import CLIManifest
from ...models.cli_publish_request import CLIPublishRequest
from ...models.error import Error
from ...types import Response


def _get_kwargs(
    *,
    body: CLIPublishRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/openapi/v1/cli",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> CLIManifest | Error | None:
    if response.status_code == 201:
        response_201 = CLIManifest.from_dict(response.json())

        return response_201

    if response.status_code == 400:
        response_400 = Error.from_dict(response.json())

        return response_400

    if response.status_code == 403:
        response_403 = Error.from_dict(response.json())

        return response_403

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
    body: CLIPublishRequest,
) -> Response[CLIManifest | Error]:
    """Publish a new arena CLI release (root only)

     Root only. Records the latest CLI release manifest. Upload each platform binary via POST
    /openapi/v1/uploads first, then reference the returned uploads/ URIs (or absolute http(s) URLs) in
    downloads[].

    Args:
        body (CLIPublishRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[CLIManifest | Error]
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
    body: CLIPublishRequest,
) -> CLIManifest | Error | None:
    """Publish a new arena CLI release (root only)

     Root only. Records the latest CLI release manifest. Upload each platform binary via POST
    /openapi/v1/uploads first, then reference the returned uploads/ URIs (or absolute http(s) URLs) in
    downloads[].

    Args:
        body (CLIPublishRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        CLIManifest | Error
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: CLIPublishRequest,
) -> Response[CLIManifest | Error]:
    """Publish a new arena CLI release (root only)

     Root only. Records the latest CLI release manifest. Upload each platform binary via POST
    /openapi/v1/uploads first, then reference the returned uploads/ URIs (or absolute http(s) URLs) in
    downloads[].

    Args:
        body (CLIPublishRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[CLIManifest | Error]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: CLIPublishRequest,
) -> CLIManifest | Error | None:
    """Publish a new arena CLI release (root only)

     Root only. Records the latest CLI release manifest. Upload each platform binary via POST
    /openapi/v1/uploads first, then reference the returned uploads/ URIs (or absolute http(s) URLs) in
    downloads[].

    Args:
        body (CLIPublishRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        CLIManifest | Error
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
