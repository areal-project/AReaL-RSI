from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.stream_stats import StreamStats
from ...types import UNSET, Response, Unset


def _get_kwargs(
    stream_id: str,
    *,
    experiment: str | Unset = UNSET,
    all_experiments: bool | Unset = False,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["experiment"] = experiment

    params["all_experiments"] = all_experiments

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/openapi/v1/streams/{stream_id}/stats".format(
            stream_id=quote(str(stream_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | StreamStats:
    if response.status_code == 200:
        response_200 = StreamStats.from_dict(response.json())

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | StreamStats]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    stream_id: str,
    *,
    client: AuthenticatedClient | Client,
    experiment: str | Unset = UNSET,
    all_experiments: bool | Unset = False,
) -> Response[Error | StreamStats]:
    """Get realtime Stream stats and reward points

     Counts and reward points are scoped to one experiment dimension unless all_experiments is set.

    Args:
        stream_id (str):
        experiment (str | Unset):
        all_experiments (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | StreamStats]
    """

    kwargs = _get_kwargs(
        stream_id=stream_id,
        experiment=experiment,
        all_experiments=all_experiments,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    stream_id: str,
    *,
    client: AuthenticatedClient | Client,
    experiment: str | Unset = UNSET,
    all_experiments: bool | Unset = False,
) -> Error | StreamStats | None:
    """Get realtime Stream stats and reward points

     Counts and reward points are scoped to one experiment dimension unless all_experiments is set.

    Args:
        stream_id (str):
        experiment (str | Unset):
        all_experiments (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | StreamStats
    """

    return sync_detailed(
        stream_id=stream_id,
        client=client,
        experiment=experiment,
        all_experiments=all_experiments,
    ).parsed


async def asyncio_detailed(
    stream_id: str,
    *,
    client: AuthenticatedClient | Client,
    experiment: str | Unset = UNSET,
    all_experiments: bool | Unset = False,
) -> Response[Error | StreamStats]:
    """Get realtime Stream stats and reward points

     Counts and reward points are scoped to one experiment dimension unless all_experiments is set.

    Args:
        stream_id (str):
        experiment (str | Unset):
        all_experiments (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | StreamStats]
    """

    kwargs = _get_kwargs(
        stream_id=stream_id,
        experiment=experiment,
        all_experiments=all_experiments,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    stream_id: str,
    *,
    client: AuthenticatedClient | Client,
    experiment: str | Unset = UNSET,
    all_experiments: bool | Unset = False,
) -> Error | StreamStats | None:
    """Get realtime Stream stats and reward points

     Counts and reward points are scoped to one experiment dimension unless all_experiments is set.

    Args:
        stream_id (str):
        experiment (str | Unset):
        all_experiments (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | StreamStats
    """

    return (
        await asyncio_detailed(
            stream_id=stream_id,
            client=client,
            experiment=experiment,
            all_experiments=all_experiments,
        )
    ).parsed
