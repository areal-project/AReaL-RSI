from http import HTTPStatus
from typing import Any

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.upload_manifest import UploadManifest
from ...models.upload_presign_result import UploadPresignResult
from ...types import Response


def _get_kwargs(
    *,
    body: UploadManifest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/openapi/v1/uploads/presign",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | UploadPresignResult:
    if response.status_code == 201:
        response_201 = UploadPresignResult.from_dict(response.json())

        return response_201

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | UploadPresignResult]:
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
) -> Response[Error | UploadPresignResult]:
    """Get presigned PUT URLs for a direct (client → OSS) upload

     Declares an upload manifest and receives one presigned PUT URL per file under a server-generated
    scratch base. Clients PUT bytes straight to OSS (send exactly the returned headers; Content-MD5 is
    part of the signature) and then call /openapi/v1/uploads/complete. Answers 501 when direct upload is
    disabled (OSS_PRESIGN_ENDPOINT unset); fall back to the legacy multipart POST /openapi/v1/uploads.

    Args:
        body (UploadManifest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | UploadPresignResult]
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
) -> Error | UploadPresignResult | None:
    """Get presigned PUT URLs for a direct (client → OSS) upload

     Declares an upload manifest and receives one presigned PUT URL per file under a server-generated
    scratch base. Clients PUT bytes straight to OSS (send exactly the returned headers; Content-MD5 is
    part of the signature) and then call /openapi/v1/uploads/complete. Answers 501 when direct upload is
    disabled (OSS_PRESIGN_ENDPOINT unset); fall back to the legacy multipart POST /openapi/v1/uploads.

    Args:
        body (UploadManifest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | UploadPresignResult
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: UploadManifest,
) -> Response[Error | UploadPresignResult]:
    """Get presigned PUT URLs for a direct (client → OSS) upload

     Declares an upload manifest and receives one presigned PUT URL per file under a server-generated
    scratch base. Clients PUT bytes straight to OSS (send exactly the returned headers; Content-MD5 is
    part of the signature) and then call /openapi/v1/uploads/complete. Answers 501 when direct upload is
    disabled (OSS_PRESIGN_ENDPOINT unset); fall back to the legacy multipart POST /openapi/v1/uploads.

    Args:
        body (UploadManifest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | UploadPresignResult]
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
) -> Error | UploadPresignResult | None:
    """Get presigned PUT URLs for a direct (client → OSS) upload

     Declares an upload manifest and receives one presigned PUT URL per file under a server-generated
    scratch base. Clients PUT bytes straight to OSS (send exactly the returned headers; Content-MD5 is
    part of the signature) and then call /openapi/v1/uploads/complete. Answers 501 when direct upload is
    disabled (OSS_PRESIGN_ENDPOINT unset); fall back to the legacy multipart POST /openapi/v1/uploads.

    Args:
        body (UploadManifest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | UploadPresignResult
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
