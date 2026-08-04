from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.model_service_instance_probe_request import (
    ModelServiceInstanceProbeRequest,
)
from ...models.model_service_instance_probe_response import (
    ModelServiceInstanceProbeResponse,
)
from ...types import Response


def _get_kwargs(
    service_id: str,
    instance_id: str,
    *,
    body: ModelServiceInstanceProbeRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/openapi/v1/model-services/{service_id}/instances/{instance_id}/probe".format(
            service_id=quote(str(service_id), safe=""),
            instance_id=quote(str(instance_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | ModelServiceInstanceProbeResponse:
    if response.status_code == 200:
        response_200 = ModelServiceInstanceProbeResponse.from_dict(response.json())

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | ModelServiceInstanceProbeResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    service_id: str,
    instance_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: ModelServiceInstanceProbeRequest,
) -> Response[Error | ModelServiceInstanceProbeResponse]:
    """Probe one READY model service instance through the control plane

     The API server forwards a non-streaming OpenAI Chat, OpenAI Responses, or Anthropic Messages request
    to the instance's registered SGLang endpoint.

    Args:
        service_id (str):
        instance_id (str):
        body (ModelServiceInstanceProbeRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | ModelServiceInstanceProbeResponse]
    """

    kwargs = _get_kwargs(
        service_id=service_id,
        instance_id=instance_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    service_id: str,
    instance_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: ModelServiceInstanceProbeRequest,
) -> Error | ModelServiceInstanceProbeResponse | None:
    """Probe one READY model service instance through the control plane

     The API server forwards a non-streaming OpenAI Chat, OpenAI Responses, or Anthropic Messages request
    to the instance's registered SGLang endpoint.

    Args:
        service_id (str):
        instance_id (str):
        body (ModelServiceInstanceProbeRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | ModelServiceInstanceProbeResponse
    """

    return sync_detailed(
        service_id=service_id,
        instance_id=instance_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    service_id: str,
    instance_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: ModelServiceInstanceProbeRequest,
) -> Response[Error | ModelServiceInstanceProbeResponse]:
    """Probe one READY model service instance through the control plane

     The API server forwards a non-streaming OpenAI Chat, OpenAI Responses, or Anthropic Messages request
    to the instance's registered SGLang endpoint.

    Args:
        service_id (str):
        instance_id (str):
        body (ModelServiceInstanceProbeRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | ModelServiceInstanceProbeResponse]
    """

    kwargs = _get_kwargs(
        service_id=service_id,
        instance_id=instance_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    service_id: str,
    instance_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: ModelServiceInstanceProbeRequest,
) -> Error | ModelServiceInstanceProbeResponse | None:
    """Probe one READY model service instance through the control plane

     The API server forwards a non-streaming OpenAI Chat, OpenAI Responses, or Anthropic Messages request
    to the instance's registered SGLang endpoint.

    Args:
        service_id (str):
        instance_id (str):
        body (ModelServiceInstanceProbeRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | ModelServiceInstanceProbeResponse
    """

    return (
        await asyncio_detailed(
            service_id=service_id,
            instance_id=instance_id,
            client=client,
            body=body,
        )
    ).parsed
