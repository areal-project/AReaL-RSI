from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.skill_version import SkillVersion
from ...models.update_skill_version_request import UpdateSkillVersionRequest
from ...types import Response


def _get_kwargs(
    key: str,
    version: str,
    *,
    body: UpdateSkillVersionRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "patch",
        "url": "/openapi/v1/skills/{key}/versions/{version}".format(
            key=quote(str(key), safe=""),
            version=quote(str(version), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | SkillVersion:
    if response.status_code == 200:
        response_200 = SkillVersion.from_dict(response.json())

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | SkillVersion]:
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
    body: UpdateSkillVersionRequest,
) -> Response[Error | SkillVersion]:
    """Update a draft Skill version

    Args:
        key (str):
        version (str):  Example: 1.0.0.
        body (UpdateSkillVersionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | SkillVersion]
    """

    kwargs = _get_kwargs(
        key=key,
        version=version,
        body=body,
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
    body: UpdateSkillVersionRequest,
) -> Error | SkillVersion | None:
    """Update a draft Skill version

    Args:
        key (str):
        version (str):  Example: 1.0.0.
        body (UpdateSkillVersionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | SkillVersion
    """

    return sync_detailed(
        key=key,
        version=version,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    key: str,
    version: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateSkillVersionRequest,
) -> Response[Error | SkillVersion]:
    """Update a draft Skill version

    Args:
        key (str):
        version (str):  Example: 1.0.0.
        body (UpdateSkillVersionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | SkillVersion]
    """

    kwargs = _get_kwargs(
        key=key,
        version=version,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    key: str,
    version: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateSkillVersionRequest,
) -> Error | SkillVersion | None:
    """Update a draft Skill version

    Args:
        key (str):
        version (str):  Example: 1.0.0.
        body (UpdateSkillVersionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | SkillVersion
    """

    return (
        await asyncio_detailed(
            key=key,
            version=version,
            client=client,
            body=body,
        )
    ).parsed
