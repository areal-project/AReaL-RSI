from http import HTTPStatus
from typing import Any

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.llm_gateway_request import LLMGatewayRequest
from ...models.llm_gateway_response import LLMGatewayResponse
from ...types import Response


def _get_kwargs(
    *,
    body: LLMGatewayRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/chat/completions",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | LLMGatewayResponse:
    if response.status_code == 200:
        response_200 = LLMGatewayResponse.from_dict(response.json())

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | LLMGatewayResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient,
    body: LLMGatewayRequest,
) -> Response[Error | LLMGatewayResponse]:
    """OpenAI-compatible chat completions through the embedded LLM gateway

     Sends an OpenAI Chat Completions request to the configured model alias. Set stream=true to receive
    an unbuffered Server-Sent Events response. Authenticate with Authorization: Bearer (an Arena token
    or enabled endpoint key), or x-api-key (an enabled endpoint key).

    Args:
        body (LLMGatewayRequest): Protocol-native request body. Fields other than model and stream
            pass through to the OpenAI Chat Completions, OpenAI Responses, or Anthropic Messages
            adapter.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | LLMGatewayResponse]
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
    client: AuthenticatedClient,
    body: LLMGatewayRequest,
) -> Error | LLMGatewayResponse | None:
    """OpenAI-compatible chat completions through the embedded LLM gateway

     Sends an OpenAI Chat Completions request to the configured model alias. Set stream=true to receive
    an unbuffered Server-Sent Events response. Authenticate with Authorization: Bearer (an Arena token
    or enabled endpoint key), or x-api-key (an enabled endpoint key).

    Args:
        body (LLMGatewayRequest): Protocol-native request body. Fields other than model and stream
            pass through to the OpenAI Chat Completions, OpenAI Responses, or Anthropic Messages
            adapter.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | LLMGatewayResponse
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient,
    body: LLMGatewayRequest,
) -> Response[Error | LLMGatewayResponse]:
    """OpenAI-compatible chat completions through the embedded LLM gateway

     Sends an OpenAI Chat Completions request to the configured model alias. Set stream=true to receive
    an unbuffered Server-Sent Events response. Authenticate with Authorization: Bearer (an Arena token
    or enabled endpoint key), or x-api-key (an enabled endpoint key).

    Args:
        body (LLMGatewayRequest): Protocol-native request body. Fields other than model and stream
            pass through to the OpenAI Chat Completions, OpenAI Responses, or Anthropic Messages
            adapter.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | LLMGatewayResponse]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient,
    body: LLMGatewayRequest,
) -> Error | LLMGatewayResponse | None:
    """OpenAI-compatible chat completions through the embedded LLM gateway

     Sends an OpenAI Chat Completions request to the configured model alias. Set stream=true to receive
    an unbuffered Server-Sent Events response. Authenticate with Authorization: Bearer (an Arena token
    or enabled endpoint key), or x-api-key (an enabled endpoint key).

    Args:
        body (LLMGatewayRequest): Protocol-native request body. Fields other than model and stream
            pass through to the OpenAI Chat Completions, OpenAI Responses, or Anthropic Messages
            adapter.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | LLMGatewayResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
