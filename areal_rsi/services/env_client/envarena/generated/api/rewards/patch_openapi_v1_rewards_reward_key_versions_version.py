from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.reward_version import RewardVersion
from ...models.update_reward_version_request import UpdateRewardVersionRequest
from ...types import Response


def _get_kwargs(
    reward_key: str,
    version: str,
    *,
    body: UpdateRewardVersionRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "patch",
        "url": "/openapi/v1/rewards/{reward_key}/versions/{version}".format(
            reward_key=quote(str(reward_key), safe=""),
            version=quote(str(version), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | RewardVersion:
    if response.status_code == 200:
        response_200 = RewardVersion.from_dict(response.json())

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | RewardVersion]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    reward_key: str,
    version: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateRewardVersionRequest,
) -> Response[Error | RewardVersion]:
    """Update a draft Reward version

    Args:
        reward_key (str):
        version (str):  Example: 1.0.0.
        body (UpdateRewardVersionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | RewardVersion]
    """

    kwargs = _get_kwargs(
        reward_key=reward_key,
        version=version,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    reward_key: str,
    version: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateRewardVersionRequest,
) -> Error | RewardVersion | None:
    """Update a draft Reward version

    Args:
        reward_key (str):
        version (str):  Example: 1.0.0.
        body (UpdateRewardVersionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | RewardVersion
    """

    return sync_detailed(
        reward_key=reward_key,
        version=version,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    reward_key: str,
    version: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateRewardVersionRequest,
) -> Response[Error | RewardVersion]:
    """Update a draft Reward version

    Args:
        reward_key (str):
        version (str):  Example: 1.0.0.
        body (UpdateRewardVersionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | RewardVersion]
    """

    kwargs = _get_kwargs(
        reward_key=reward_key,
        version=version,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    reward_key: str,
    version: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateRewardVersionRequest,
) -> Error | RewardVersion | None:
    """Update a draft Reward version

    Args:
        reward_key (str):
        version (str):  Example: 1.0.0.
        body (UpdateRewardVersionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | RewardVersion
    """

    return (
        await asyncio_detailed(
            reward_key=reward_key,
            version=version,
            client=client,
            body=body,
        )
    ).parsed
