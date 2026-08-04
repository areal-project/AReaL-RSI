from http import HTTPStatus
from typing import Any

import httpx

from ...client import AuthenticatedClient, Client
from ...models.create_model_service_request import CreateModelServiceRequest
from ...models.error import Error
from ...models.model_service_command_preview import ModelServiceCommandPreview
from ...types import Response


def _get_kwargs(
    *,
    body: CreateModelServiceRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/openapi/v1/model-services/command-preview",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | ModelServiceCommandPreview:
    if response.status_code == 200:
        response_200 = ModelServiceCommandPreview.from_dict(response.json())

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | ModelServiceCommandPreview]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: CreateModelServiceRequest,
) -> Response[Error | ModelServiceCommandPreview]:
    """Preview the SGLang launch command for a model service

     Calls the internal AIS bridge command builder without creating service rows or submitting an AIS
    job.

    Args:
        body (CreateModelServiceRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | ModelServiceCommandPreview]
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
    body: CreateModelServiceRequest,
) -> Error | ModelServiceCommandPreview | None:
    """Preview the SGLang launch command for a model service

     Calls the internal AIS bridge command builder without creating service rows or submitting an AIS
    job.

    Args:
        body (CreateModelServiceRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | ModelServiceCommandPreview
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: CreateModelServiceRequest,
) -> Response[Error | ModelServiceCommandPreview]:
    """Preview the SGLang launch command for a model service

     Calls the internal AIS bridge command builder without creating service rows or submitting an AIS
    job.

    Args:
        body (CreateModelServiceRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | ModelServiceCommandPreview]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: CreateModelServiceRequest,
) -> Error | ModelServiceCommandPreview | None:
    """Preview the SGLang launch command for a model service

     Calls the internal AIS bridge command builder without creating service rows or submitting an AIS
    job.

    Args:
        body (CreateModelServiceRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | ModelServiceCommandPreview
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
