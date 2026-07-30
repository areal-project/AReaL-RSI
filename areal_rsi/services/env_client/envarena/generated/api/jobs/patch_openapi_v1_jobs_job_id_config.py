from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.update_job_config_request import UpdateJobConfigRequest
from ...models.update_job_config_response import UpdateJobConfigResponse
from ...types import Response


def _get_kwargs(
    job_id: str,
    *,
    body: UpdateJobConfigRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "patch",
        "url": "/openapi/v1/jobs/{job_id}/config".format(
            job_id=quote(str(job_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | UpdateJobConfigResponse:
    if response.status_code == 200:
        response_200 = UpdateJobConfigResponse.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = Error.from_dict(response.json())

        return response_400

    if response.status_code == 409:
        response_409 = Error.from_dict(response.json())

        return response_409

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | UpdateJobConfigResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    job_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateJobConfigRequest,
) -> Response[Error | UpdateJobConfigResponse]:
    """Update the runtime concurrency/retry config of a submitted job

     Mutates max_parallel, max_retries and retry_backoff_seconds of a job
    under the config_revision optimistic lock. Omitted fields keep their
    current values. Changes take effect on the live run: the scheduler
    re-reads max_parallel every few seconds and the retry loop re-reads
    the retry policy before every retry decision.

    Args:
        job_id (str):
        body (UpdateJobConfigRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | UpdateJobConfigResponse]
    """

    kwargs = _get_kwargs(
        job_id=job_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    job_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateJobConfigRequest,
) -> Error | UpdateJobConfigResponse | None:
    """Update the runtime concurrency/retry config of a submitted job

     Mutates max_parallel, max_retries and retry_backoff_seconds of a job
    under the config_revision optimistic lock. Omitted fields keep their
    current values. Changes take effect on the live run: the scheduler
    re-reads max_parallel every few seconds and the retry loop re-reads
    the retry policy before every retry decision.

    Args:
        job_id (str):
        body (UpdateJobConfigRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | UpdateJobConfigResponse
    """

    return sync_detailed(
        job_id=job_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    job_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateJobConfigRequest,
) -> Response[Error | UpdateJobConfigResponse]:
    """Update the runtime concurrency/retry config of a submitted job

     Mutates max_parallel, max_retries and retry_backoff_seconds of a job
    under the config_revision optimistic lock. Omitted fields keep their
    current values. Changes take effect on the live run: the scheduler
    re-reads max_parallel every few seconds and the retry loop re-reads
    the retry policy before every retry decision.

    Args:
        job_id (str):
        body (UpdateJobConfigRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | UpdateJobConfigResponse]
    """

    kwargs = _get_kwargs(
        job_id=job_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    job_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateJobConfigRequest,
) -> Error | UpdateJobConfigResponse | None:
    """Update the runtime concurrency/retry config of a submitted job

     Mutates max_parallel, max_retries and retry_backoff_seconds of a job
    under the config_revision optimistic lock. Omitted fields keep their
    current values. Changes take effect on the live run: the scheduler
    re-reads max_parallel every few seconds and the retry loop re-reads
    the retry policy before every retry decision.

    Args:
        job_id (str):
        body (UpdateJobConfigRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | UpdateJobConfigResponse
    """

    return (
        await asyncio_detailed(
            job_id=job_id,
            client=client,
            body=body,
        )
    ).parsed
