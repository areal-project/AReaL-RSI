from http import HTTPStatus
from typing import Any

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.import_skill_url_request import ImportSkillURLRequest
from ...models.skill_version import SkillVersion
from ...types import Response


def _get_kwargs(
    *,
    body: ImportSkillURLRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/openapi/v1/skills/import-url",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | SkillVersion:
    if response.status_code == 201:
        response_201 = SkillVersion.from_dict(response.json())

        return response_201

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
    *,
    client: AuthenticatedClient | Client,
    body: ImportSkillURLRequest,
) -> Response[Error | SkillVersion]:
    """Import a Skill from a URL

     Imports a zip archive or raw SKILL.md that follows the agentskills.io SKILL.md frontmatter shape.

    Args:
        body (ImportSkillURLRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | SkillVersion]
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
    body: ImportSkillURLRequest,
) -> Error | SkillVersion | None:
    """Import a Skill from a URL

     Imports a zip archive or raw SKILL.md that follows the agentskills.io SKILL.md frontmatter shape.

    Args:
        body (ImportSkillURLRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | SkillVersion
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: ImportSkillURLRequest,
) -> Response[Error | SkillVersion]:
    """Import a Skill from a URL

     Imports a zip archive or raw SKILL.md that follows the agentskills.io SKILL.md frontmatter shape.

    Args:
        body (ImportSkillURLRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | SkillVersion]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: ImportSkillURLRequest,
) -> Error | SkillVersion | None:
    """Import a Skill from a URL

     Imports a zip archive or raw SKILL.md that follows the agentskills.io SKILL.md frontmatter shape.

    Args:
        body (ImportSkillURLRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | SkillVersion
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
