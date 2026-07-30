from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.model_version import ModelVersion
from ...types import Response


def _get_kwargs(
    model_key: str,
    version: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/openapi/v1/models/{model_key}/versions/{version}/publish".format(
            model_key=quote(str(model_key), safe=""),
            version=quote(str(version), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | ModelVersion:
    if response.status_code == 200:
        response_200 = ModelVersion.from_dict(response.json())

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | ModelVersion]:
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
) -> Response[Error | ModelVersion]:
    """Publish an immutable Model version

     Publishes a compact model runtime config. model_name is required for Job launch.

    Args:
        model_key (str):
        version (str):  Example: 1.0.0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | ModelVersion]
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
) -> Error | ModelVersion | None:
    """Publish an immutable Model version

     Publishes a compact model runtime config. model_name is required for Job launch.

    Args:
        model_key (str):
        version (str):  Example: 1.0.0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | ModelVersion
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
) -> Response[Error | ModelVersion]:
    """Publish an immutable Model version

     Publishes a compact model runtime config. model_name is required for Job launch.

    Args:
        model_key (str):
        version (str):  Example: 1.0.0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | ModelVersion]
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
) -> Error | ModelVersion | None:
    """Publish an immutable Model version

     Publishes a compact model runtime config. model_name is required for Job launch.

    Args:
        model_key (str):
        version (str):  Example: 1.0.0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | ModelVersion
    """

    return (
        await asyncio_detailed(
            model_key=model_key,
            version=version,
            client=client,
        )
    ).parsed
