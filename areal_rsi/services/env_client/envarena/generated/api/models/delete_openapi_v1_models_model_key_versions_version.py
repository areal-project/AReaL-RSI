from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...types import Response


def _get_kwargs(
    model_key: str,
    version: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "delete",
        "url": "/openapi/v1/models/{model_key}/versions/{version}".format(
            model_key=quote(str(model_key), safe=""),
            version=quote(str(version), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | Error:
    if response.status_code == 204:
        response_204 = cast(Any, None)
        return response_204

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Any | Error]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    model_key: str,
    version: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[Any | Error]:
    """Soft-delete a Model version

     Marks the version as DELETED while retaining its immutable spec.

    Args:
        model_key (str):
        version (str):  Example: 1.0.0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | Error]
    """

    kwargs = _get_kwargs(
        model_key=model_key,
        version=version,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    model_key: str,
    version: str,
    *,
    client: AuthenticatedClient | Client,
) -> Any | Error | None:
    """Soft-delete a Model version

     Marks the version as DELETED while retaining its immutable spec.

    Args:
        model_key (str):
        version (str):  Example: 1.0.0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | Error
    """

    return sync_detailed(
        model_key=model_key,
        version=version,
        client=client,
    ).parsed


async def asyncio_detailed(
    model_key: str,
    version: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[Any | Error]:
    """Soft-delete a Model version

     Marks the version as DELETED while retaining its immutable spec.

    Args:
        model_key (str):
        version (str):  Example: 1.0.0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | Error]
    """

    kwargs = _get_kwargs(
        model_key=model_key,
        version=version,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    model_key: str,
    version: str,
    *,
    client: AuthenticatedClient | Client,
) -> Any | Error | None:
    """Soft-delete a Model version

     Marks the version as DELETED while retaining its immutable spec.

    Args:
        model_key (str):
        version (str):  Example: 1.0.0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | Error
    """

    return (
        await asyncio_detailed(
            model_key=model_key,
            version=version,
            client=client,
        )
    ).parsed
