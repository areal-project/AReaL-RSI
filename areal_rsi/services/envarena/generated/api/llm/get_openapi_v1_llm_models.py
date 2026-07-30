from http import HTTPStatus
from typing import Any

import httpx

from ...client import AuthenticatedClient, Client
from ...models.error import Error
from ...models.llm_model_list_response import LLMModelListResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    disable_stream: bool | Unset = False,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["disable-stream"] = disable_stream

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/openapi/v1/llm/models",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | LLMModelListResponse:
    if response.status_code == 200:
        response_200 = LLMModelListResponse.from_dict(response.json())

        return response_200

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | LLMModelListResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    disable_stream: bool | Unset = False,
) -> Response[Error | LLMModelListResponse]:
    """List configured LLM gateway models

     Returns configured model routes. Set disable-stream=true to omit dynamically registered model names
    beginning with stream-areal. api_key is always masked in responses.

    Args:
        disable_stream (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | LLMModelListResponse]
    """

    kwargs = _get_kwargs(
        disable_stream=disable_stream,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    disable_stream: bool | Unset = False,
) -> Error | LLMModelListResponse | None:
    """List configured LLM gateway models

     Returns configured model routes. Set disable-stream=true to omit dynamically registered model names
    beginning with stream-areal. api_key is always masked in responses.

    Args:
        disable_stream (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | LLMModelListResponse
    """

    return sync_detailed(
        client=client,
        disable_stream=disable_stream,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    disable_stream: bool | Unset = False,
) -> Response[Error | LLMModelListResponse]:
    """List configured LLM gateway models

     Returns configured model routes. Set disable-stream=true to omit dynamically registered model names
    beginning with stream-areal. api_key is always masked in responses.

    Args:
        disable_stream (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | LLMModelListResponse]
    """

    kwargs = _get_kwargs(
        disable_stream=disable_stream,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    disable_stream: bool | Unset = False,
) -> Error | LLMModelListResponse | None:
    """List configured LLM gateway models

     Returns configured model routes. Set disable-stream=true to omit dynamically registered model names
    beginning with stream-areal. api_key is always masked in responses.

    Args:
        disable_stream (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | LLMModelListResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            disable_stream=disable_stream,
        )
    ).parsed
