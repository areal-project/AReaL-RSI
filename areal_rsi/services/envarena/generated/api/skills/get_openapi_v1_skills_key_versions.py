from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.entity_status import EntityStatus
from ...models.error import Error
from ...models.get_openapi_v1_skills_key_versions_response_200 import (
    GetOpenapiV1SkillsKeyVersionsResponse200,
)
from ...types import UNSET, Response, Unset


def _get_kwargs(
    key: str,
    *,
    status: EntityStatus | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_status: str | Unset = UNSET
    if not isinstance(status, Unset):
        json_status = status.value

    params["status"] = json_status

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/openapi/v1/skills/{key}/versions".format(
            key=quote(str(key), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | GetOpenapiV1SkillsKeyVersionsResponse200:
    if response.status_code == 200:
        response_200 = GetOpenapiV1SkillsKeyVersionsResponse200.from_dict(
            response.json()
        )

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | GetOpenapiV1SkillsKeyVersionsResponse200]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    key: str,
    *,
    client: AuthenticatedClient | Client,
    status: EntityStatus | Unset = UNSET,
) -> Response[Error | GetOpenapiV1SkillsKeyVersionsResponse200]:
    """List Skill versions

    Args:
        key (str):
        status (EntityStatus | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | GetOpenapiV1SkillsKeyVersionsResponse200]
    """

    kwargs = _get_kwargs(
        key=key,
        status=status,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    key: str,
    *,
    client: AuthenticatedClient | Client,
    status: EntityStatus | Unset = UNSET,
) -> Error | GetOpenapiV1SkillsKeyVersionsResponse200 | None:
    """List Skill versions

    Args:
        key (str):
        status (EntityStatus | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | GetOpenapiV1SkillsKeyVersionsResponse200
    """

    return sync_detailed(
        key=key,
        client=client,
        status=status,
    ).parsed


async def asyncio_detailed(
    key: str,
    *,
    client: AuthenticatedClient | Client,
    status: EntityStatus | Unset = UNSET,
) -> Response[Error | GetOpenapiV1SkillsKeyVersionsResponse200]:
    """List Skill versions

    Args:
        key (str):
        status (EntityStatus | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | GetOpenapiV1SkillsKeyVersionsResponse200]
    """

    kwargs = _get_kwargs(
        key=key,
        status=status,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    key: str,
    *,
    client: AuthenticatedClient | Client,
    status: EntityStatus | Unset = UNSET,
) -> Error | GetOpenapiV1SkillsKeyVersionsResponse200 | None:
    """List Skill versions

    Args:
        key (str):
        status (EntityStatus | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | GetOpenapiV1SkillsKeyVersionsResponse200
    """

    return (
        await asyncio_detailed(
            key=key,
            client=client,
            status=status,
        )
    ).parsed
