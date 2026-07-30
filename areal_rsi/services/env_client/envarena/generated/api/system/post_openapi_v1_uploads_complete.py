from http import HTTPStatus
from typing import Any

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.upload_manifest import UploadManifest
from ...models.upload_result import UploadResult
from ...types import Response


def _get_kwargs(
    *,
    body: UploadManifest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/openapi/v1/uploads/complete",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | UploadResult:
    if response.status_code == 201:
        response_201 = UploadResult.from_dict(response.json())

        return response_201

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | UploadResult]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: UploadManifest,
) -> Response[Error | UploadResult]:
    """Verify a direct upload and get its final uploads/ URI

     Re-declares the manifest for a presigned upload. The server lists the scratch base and requires an
    exact match (every manifest file present with its declared size, nothing else); mismatches answer
    409.

    Args:
        body (UploadManifest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | UploadResult]
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
    body: UploadManifest,
) -> Error | UploadResult | None:
    """Verify a direct upload and get its final uploads/ URI

     Re-declares the manifest for a presigned upload. The server lists the scratch base and requires an
    exact match (every manifest file present with its declared size, nothing else); mismatches answer
    409.

    Args:
        body (UploadManifest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | UploadResult
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: UploadManifest,
) -> Response[Error | UploadResult]:
    """Verify a direct upload and get its final uploads/ URI

     Re-declares the manifest for a presigned upload. The server lists the scratch base and requires an
    exact match (every manifest file present with its declared size, nothing else); mismatches answer
    409.

    Args:
        body (UploadManifest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | UploadResult]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: UploadManifest,
) -> Error | UploadResult | None:
    """Verify a direct upload and get its final uploads/ URI

     Re-declares the manifest for a presigned upload. The server lists the scratch base and requires an
    exact match (every manifest file present with its declared size, nothing else); mismatches answer
    409.

    Args:
        body (UploadManifest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | UploadResult
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
