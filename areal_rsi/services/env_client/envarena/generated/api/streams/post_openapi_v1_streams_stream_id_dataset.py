from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.post_openapi_v1_streams_stream_id_dataset_body import (
    PostOpenapiV1StreamsStreamIdDatasetBody,
)
from ...models.post_openapi_v1_streams_stream_id_dataset_response_200 import (
    PostOpenapiV1StreamsStreamIdDatasetResponse200,
)
from ...types import UNSET, Response, Unset


def _get_kwargs(
    stream_id: str,
    *,
    body: PostOpenapiV1StreamsStreamIdDatasetBody | Unset = UNSET,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    params: dict[str, Any] = {}

    params["limit"] = limit

    params["offset"] = offset

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/openapi/v1/streams/{stream_id}/dataset".format(
            stream_id=quote(str(stream_id), safe=""),
        ),
        "params": params,
    }

    if not isinstance(body, Unset):
        _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | PostOpenapiV1StreamsStreamIdDatasetResponse200:
    if response.status_code == 200:
        response_200 = PostOpenapiV1StreamsStreamIdDatasetResponse200.from_dict(
            response.json()
        )

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | PostOpenapiV1StreamsStreamIdDatasetResponse200]:
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
    body: PostOpenapiV1StreamsStreamIdDatasetBody | Unset = UNSET,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
) -> Response[Error | PostOpenapiV1StreamsStreamIdDatasetResponse200]:
    """Select online dataset data_ids for a Stream

     Applies the Stream selector plus request overrides. Selector supports partition, include_tags,
    exclude_tags, and optional created_at bounds; no raw SQL is accepted.

    Args:
        stream_id (str):
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.
        body (PostOpenapiV1StreamsStreamIdDatasetBody | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | PostOpenapiV1StreamsStreamIdDatasetResponse200]
    """

    kwargs = _get_kwargs(
        stream_id=stream_id,
        body=body,
        limit=limit,
        offset=offset,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    stream_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: PostOpenapiV1StreamsStreamIdDatasetBody | Unset = UNSET,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
) -> Error | PostOpenapiV1StreamsStreamIdDatasetResponse200 | None:
    """Select online dataset data_ids for a Stream

     Applies the Stream selector plus request overrides. Selector supports partition, include_tags,
    exclude_tags, and optional created_at bounds; no raw SQL is accepted.

    Args:
        stream_id (str):
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.
        body (PostOpenapiV1StreamsStreamIdDatasetBody | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | PostOpenapiV1StreamsStreamIdDatasetResponse200
    """

    return sync_detailed(
        stream_id=stream_id,
        client=client,
        body=body,
        limit=limit,
        offset=offset,
    ).parsed


async def asyncio_detailed(
    stream_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: PostOpenapiV1StreamsStreamIdDatasetBody | Unset = UNSET,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
) -> Response[Error | PostOpenapiV1StreamsStreamIdDatasetResponse200]:
    """Select online dataset data_ids for a Stream

     Applies the Stream selector plus request overrides. Selector supports partition, include_tags,
    exclude_tags, and optional created_at bounds; no raw SQL is accepted.

    Args:
        stream_id (str):
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.
        body (PostOpenapiV1StreamsStreamIdDatasetBody | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | PostOpenapiV1StreamsStreamIdDatasetResponse200]
    """

    kwargs = _get_kwargs(
        stream_id=stream_id,
        body=body,
        limit=limit,
        offset=offset,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    stream_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: PostOpenapiV1StreamsStreamIdDatasetBody | Unset = UNSET,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
) -> Error | PostOpenapiV1StreamsStreamIdDatasetResponse200 | None:
    """Select online dataset data_ids for a Stream

     Applies the Stream selector plus request overrides. Selector supports partition, include_tags,
    exclude_tags, and optional created_at bounds; no raw SQL is accepted.

    Args:
        stream_id (str):
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.
        body (PostOpenapiV1StreamsStreamIdDatasetBody | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | PostOpenapiV1StreamsStreamIdDatasetResponse200
    """

    return (
        await asyncio_detailed(
            stream_id=stream_id,
            client=client,
            body=body,
            limit=limit,
            offset=offset,
        )
    ).parsed
