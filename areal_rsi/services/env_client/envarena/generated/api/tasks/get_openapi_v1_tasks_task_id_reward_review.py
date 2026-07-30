from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.reward_review import RewardReview
from ...types import Response


def _get_kwargs(
    task_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/openapi/v1/tasks/{task_id}/reward-review".format(
            task_id=quote(str(task_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | RewardReview:
    if response.status_code == 200:
        response_200 = RewardReview.from_dict(response.json())

        return response_200

    if response.status_code == 404:
        response_404 = Error.from_dict(response.json())

        return response_404

    if response.status_code == 502:
        response_502 = Error.from_dict(response.json())

        return response_502

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | RewardReview]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    task_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[Error | RewardReview]:
    """Get structured X2Env Reward review

     Projects the current X2Env deterministic script report into one score card per atomic check and
    resolves only evidence declared by that report. It is available when the Task has a registered
    artifacts/x2env/reward_report.json. The script report remains the authoritative source of score and
    pass; verifier-agent information is diagnostic only. Hidden check descriptions and evidence require
    root.

    Args:
        task_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | RewardReview]
    """

    kwargs = _get_kwargs(
        task_id=task_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    task_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Error | RewardReview | None:
    """Get structured X2Env Reward review

     Projects the current X2Env deterministic script report into one score card per atomic check and
    resolves only evidence declared by that report. It is available when the Task has a registered
    artifacts/x2env/reward_report.json. The script report remains the authoritative source of score and
    pass; verifier-agent information is diagnostic only. Hidden check descriptions and evidence require
    root.

    Args:
        task_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | RewardReview
    """

    return sync_detailed(
        task_id=task_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    task_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[Error | RewardReview]:
    """Get structured X2Env Reward review

     Projects the current X2Env deterministic script report into one score card per atomic check and
    resolves only evidence declared by that report. It is available when the Task has a registered
    artifacts/x2env/reward_report.json. The script report remains the authoritative source of score and
    pass; verifier-agent information is diagnostic only. Hidden check descriptions and evidence require
    root.

    Args:
        task_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | RewardReview]
    """

    kwargs = _get_kwargs(
        task_id=task_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    task_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Error | RewardReview | None:
    """Get structured X2Env Reward review

     Projects the current X2Env deterministic script report into one score card per atomic check and
    resolves only evidence declared by that report. It is available when the Task has a registered
    artifacts/x2env/reward_report.json. The script report remains the authoritative source of score and
    pass; verifier-agent information is diagnostic only. Hidden check descriptions and evidence require
    root.

    Args:
        task_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | RewardReview
    """

    return (
        await asyncio_detailed(
            task_id=task_id,
            client=client,
        )
    ).parsed
