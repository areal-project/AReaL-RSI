from http import HTTPStatus
from typing import Any

import httpx

from ...client import AuthenticatedClient, Client
from ...models.create_llm_model_request import CreateLLMModelRequest
from ...models.error import Error
from ...models.llm_model import LLMModel
from ...types import Response


def _get_kwargs(
    *,
    body: CreateLLMModelRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/openapi/v1/llm/models",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Error | LLMModel:
    if response.status_code == 201:
        response_201 = LLMModel.from_dict(response.json())

        return response_201

    response_default = Error.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Error | LLMModel]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: CreateLLMModelRequest,
) -> Response[Error | LLMModel]:
    """Create or replace an LLM gateway model

     Any authenticated user can create or replace a live LLM gateway model route.

    Args:
        body (CreateLLMModelRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | LLMModel]
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
    body: CreateLLMModelRequest,
) -> Error | LLMModel | None:
    """Create or replace an LLM gateway model

     Any authenticated user can create or replace a live LLM gateway model route.

    Args:
        body (CreateLLMModelRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | LLMModel
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: CreateLLMModelRequest,
) -> Response[Error | LLMModel]:
    """Create or replace an LLM gateway model

     Any authenticated user can create or replace a live LLM gateway model route.

    Args:
        body (CreateLLMModelRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Error | LLMModel]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: CreateLLMModelRequest,
) -> Error | LLMModel | None:
    """Create or replace an LLM gateway model

     Any authenticated user can create or replace a live LLM gateway model route.

    Args:
        body (CreateLLMModelRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Error | LLMModel
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
