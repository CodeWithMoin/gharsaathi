from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import RedirectResponse

from app.commerce.zepto.errors import (
    ZeptoAuthenticationRequired,
    ZeptoConfigurationError,
    ZeptoProviderError,
    ZeptoSchemaError,
)
from app.commerce.zepto.models import ConnectionStatus, ToolDescription
from app.core.config import get_settings
from app.dependencies import ZeptoServiceDependency


router = APIRouter()


def _raise_http(exc: Exception) -> None:
    if isinstance(exc, ZeptoAuthenticationRequired):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    if isinstance(exc, ZeptoConfigurationError):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    if isinstance(exc, (ZeptoSchemaError, ZeptoProviderError)):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    raise exc


@router.get("/connection", response_model=ConnectionStatus)
async def connection(service: ZeptoServiceDependency) -> ConnectionStatus:
    try:
        return await service.connection_status()
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.get("/oauth/start", response_class=RedirectResponse)
async def oauth_start(service: ZeptoServiceDependency) -> RedirectResponse:
    try:
        return RedirectResponse(await service.authorization_url(), status_code=302)
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.get("/oauth/callback", response_class=RedirectResponse)
async def oauth_callback(
    service: ZeptoServiceDependency,
    state_value: Annotated[str, Query(alias="state", min_length=20, max_length=512)],
    code: Annotated[str | None, Query(min_length=1, max_length=4096)] = None,
    error: Annotated[str | None, Query(max_length=256)] = None,
    issuer: Annotated[str | None, Query(alias="iss", max_length=512)] = None,
) -> RedirectResponse:
    try:
        if error is not None:
            await service.reject_authorization(state_value)
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Zepto authorization was not completed.",
            )
        if code is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Zepto callback did not include an authorization code.",
            )
        await service.complete_authorization(code, state_value, issuer)
        frontend_url = get_settings().frontend_url.rstrip("/")
        return RedirectResponse(f"{frontend_url}/?zepto=connected", status_code=302)
    except HTTPException:
        raise
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.post("/disconnect", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect(service: ZeptoServiceDependency) -> None:
    try:
        await service.disconnect()
    except Exception as exc:
        _raise_http(exc)


@router.get("/tools", response_model=list[ToolDescription])
async def tools(service: ZeptoServiceDependency) -> list[ToolDescription]:
    try:
        return await service.list_tools()
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")
