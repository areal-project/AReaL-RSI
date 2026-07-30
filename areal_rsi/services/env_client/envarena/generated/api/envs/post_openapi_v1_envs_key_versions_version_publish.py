from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.env_version import EnvVersion
from ...models.error import Error
from ...types import Response


def _get_kwargs(
    key: str,
    version: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/openapi/v1/envs/{key}/versions/{version}/publish".format(
            key=quote(str(key), safe=""),
            version=quote(str(version), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> EnvVersion | Error:
    if response.status_code == 200:
        response_200 = EnvVersion.from_dict(response.json())

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[EnvVersion | Error]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    key: str,
    version: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[EnvVersion | Error]:
    """Publish an immutable Env version

     Requires query.uri, runtime, timeout_seconds, and bundle.uri when runtime.kind=files.

    Args:
        key (str):
        version (str):  Example: 1.0.0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[EnvVersion | Error]
    """

    kwargs = _get_kwargs(
        key=key,
        version=version,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    key: str,
    version: str,
    *,
    client: AuthenticatedClient | Client,
) -> EnvVersion | Error | None:
    """Publish an immutable Env version

     Requires query.uri, runtime, timeout_seconds, and bundle.uri when runtime.kind=files.

    Args:
        key (str):
        version (str):  Example: 1.0.0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        EnvVersion | Error
    """

    return sync_detailed(
        key=key,
        version=version,
        client=client,
    ).parsed


async def asyncio_detailed(
    key: str,
    version: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[EnvVersion | Error]:
    """Publish an immutable Env version

     Requires query.uri, runtime, timeout_seconds, and bundle.uri when runtime.kind=files.

    Args:
        key (str):
        version (str):  Example: 1.0.0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[EnvVersion | Error]
    """

    kwargs = _get_kwargs(
        key=key,
        version=version,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    key: str,
    version: str,
    *,
    client: AuthenticatedClient | Client,
) -> EnvVersion | Error | None:
    """Publish an immutable Env version

     Requires query.uri, runtime, timeout_seconds, and bundle.uri when runtime.kind=files.

    Args:
        key (str):
        version (str):  Example: 1.0.0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        EnvVersion | Error
    """

    return (
        await asyncio_detailed(
            key=key,
            version=version,
            client=client,
        )
    ).parsed
