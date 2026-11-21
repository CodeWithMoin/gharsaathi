from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, field_validator

from app.commerce.swiggy.errors import (
    SwiggyAuthenticationRequired,
    SwiggyConfigurationError,
    SwiggyProviderError,
    SwiggySchemaError,
)
from app.commerce.swiggy.models import (
    AddressList,
    ConnectionStatus,
    ProductSearchResult,
    ToolDescription,
)
from app.core.config import get_settings
from app.dependencies import SwiggyServiceDependency


router = APIRouter()


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=120)
    address_id: str = Field(min_length=1, max_length=256)
    offset: int = Field(default=0, ge=0)

    @field_validator("query", "address_id")
    @classmethod
    def strip_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


def _raise_http(exc: Exception) -> None:
    if isinstance(exc, SwiggyAuthenticationRequired):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    if isinstance(exc, SwiggyConfigurationError):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    if isinstance(exc, (SwiggySchemaError, SwiggyProviderError)):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    raise exc


@router.get("/connection", response_model=ConnectionStatus)
async def connection(service: SwiggyServiceDependency) -> ConnectionStatus:
    try:
        return await service.connection_status()
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.get("/oauth/start", response_class=RedirectResponse)
async def oauth_start(service: SwiggyServiceDependency) -> RedirectResponse:
    try:
        return RedirectResponse(await service.authorization_url(), status_code=302)
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.get("/oauth/callback", response_class=RedirectResponse)
async def oauth_callback(
    service: SwiggyServiceDependency,
    state_value: Annotated[str, Query(alias="state", min_length=20, max_length=512)],
    code: Annotated[str | None, Query(min_length=1, max_length=4096)] = None,
    error: Annotated[str | None, Query(max_length=256)] = None,
) -> RedirectResponse:
    try:
        if error is not None:
            await service.reject_authorization(state_value)
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Swiggy authorization was not completed.",
            )
        if code is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Swiggy callback did not include an authorization code.",
            )
        await service.complete_authorization(code, state_value)
        frontend_url = get_settings().frontend_url.rstrip("/")
        return RedirectResponse(f"{frontend_url}/?swiggy=connected", status_code=302)
    except HTTPException:
        raise
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.post("/disconnect", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect(service: SwiggyServiceDependency) -> None:
    try:
        await service.disconnect()
    except Exception as exc:
        _raise_http(exc)


@router.get("/tools", response_model=list[ToolDescription])
async def tools(service: SwiggyServiceDependency) -> list[ToolDescription]:
    try:
        return await service.list_tools()
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.get("/addresses", response_model=AddressList)
async def addresses(
    service: SwiggyServiceDependency,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=10)] = 10,
) -> AddressList:
    try:
        return await service.addresses(page, page_size)
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.post("/search", response_model=ProductSearchResult)
async def search(
    body: SearchRequest, service: SwiggyServiceDependency
) -> ProductSearchResult:
    try:
        return await service.search(body.query, body.address_id, body.offset)
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")

