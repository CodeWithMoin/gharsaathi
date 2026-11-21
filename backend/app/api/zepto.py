from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, field_validator

from app.commerce.demo import DemoZeptoService
from app.commerce.provider_state import (
    ProviderAddressRepository,
    cached_address_list,
    is_demo_address_state,
)
from app.commerce.swiggy.models import (
    AddressList,
    AddressResolution,
    Pagination,
    ProductSearchResult,
)
from app.commerce.zepto.errors import (
    ZeptoAuthenticationRequired,
    ZeptoConfigurationError,
    ZeptoProviderError,
    ZeptoRateLimited,
    ZeptoSchemaError,
)
from app.commerce.query_guard import non_search_reason
from app.commerce.zepto.cart import ZeptoCart
from app.commerce.zepto.history import ZeptoOrderDetail, ZeptoOrderHistory
from app.commerce.zepto.models import (
    CapabilitySummary,
    ConnectionStatus,
    ToolDescription,
)
from app.core.config import get_settings
from app.dependencies import ZeptoServiceDependency
from app.memory.dependencies import DbSessionDependency, MemoryActorDependency
from app.oauth_readiness import local_callback_is_ready


router = APIRouter()
ZEPTO_PROVIDER = "zepto"


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=120)
    address_id: str = Field(min_length=1, max_length=256)
    page_number: int = Field(default=0, ge=0, le=20)

    @field_validator("query", "address_id")
    @classmethod
    def strip_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class SelectedAddressRequest(BaseModel):
    address_id: str = Field(min_length=1, max_length=256)


def _raise_http(exc: Exception) -> None:
    if isinstance(exc, ZeptoAuthenticationRequired):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    if isinstance(exc, ZeptoConfigurationError):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    if isinstance(exc, ZeptoRateLimited):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc
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
        settings = get_settings()
        if not await local_callback_is_ready(
            settings.zepto_oauth_redirect_uri,
            expected_service="oauth-callback-bridge",
        ):
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Zepto's local OAuth callback bridge is not running. Run `make zepto-bridge` first.",
            )
        return RedirectResponse(await service.authorization_url(), status_code=302)
    except HTTPException:
        raise
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.get("/oauth/readiness")
async def oauth_readiness() -> dict[str, bool | str]:
    ready = await local_callback_is_ready(
        get_settings().zepto_oauth_redirect_uri,
        expected_service="oauth-callback-bridge",
    )
    return {
        "ready": ready,
        "message": (
            "Zepto is ready to connect."
            if ready
            else "Start `make zepto-bridge` and keep that terminal open before connecting Zepto."
        ),
    }


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
async def disconnect(
    service: ZeptoServiceDependency,
    session: DbSessionDependency,
    actor: MemoryActorDependency,
) -> None:
    try:
        await service.disconnect()
        await ProviderAddressRepository(session).clear(actor.id, ZEPTO_PROVIDER)
    except Exception as exc:
        _raise_http(exc)


@router.get("/tools", response_model=list[ToolDescription])
async def tools(service: ZeptoServiceDependency) -> list[ToolDescription]:
    try:
        return await service.list_tools()
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.get("/capabilities", response_model=CapabilitySummary)
async def capabilities(service: ZeptoServiceDependency) -> CapabilitySummary:
    """Authenticated tool discovery with an explicit no-checkout boundary."""

    try:
        return await service.capabilities()
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.get("/cart", response_model=ZeptoCart)
async def cart(service: ZeptoServiceDependency) -> ZeptoCart:
    """Return a display-only provider-cart snapshot; never mutate it."""

    try:
        return await service.cart()
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.get("/orders", response_model=ZeptoOrderHistory)
async def order_history(
    service: ZeptoServiceDependency,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    page_number: Annotated[int, Query(ge=1, le=1000)] = 1,
) -> ZeptoOrderHistory:
    """Return normalized Zepto order history, distinct from repeat-item hints."""

    try:
        return await service.order_history(limit=limit, page_number=page_number)
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.get(
    "/orders/{order_id}",
    response_model=ZeptoOrderDetail,
    response_model_by_alias=False,
)
async def order_detail(
    order_id: Annotated[str, Path(min_length=1, max_length=256)],
    service: ZeptoServiceDependency,
) -> ZeptoOrderDetail:
    """Return one authenticated Zepto order's provider-confirmed details."""

    try:
        return await service.order_detail(order_id.strip())
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.get("/addresses", response_model=AddressList)
async def addresses(
    service: ZeptoServiceDependency,
    session: DbSessionDependency,
    actor: MemoryActorDependency,
    refresh: Annotated[bool, Query()] = False,
) -> AddressList:
    try:
        if isinstance(service, DemoZeptoService):
            return await service.addresses()
        repository = ProviderAddressRepository(session)
        state = await repository.get(actor.id, ZEPTO_PROVIDER)
        if state is not None and is_demo_address_state(state):
            await repository.clear(actor.id, ZEPTO_PROVIDER)
            state = None
        if state is not None and not refresh:
            return cached_address_list(state)
        if not refresh:
            return AddressList(
                addresses=[],
                pagination=Pagination(),
                resolution=AddressResolution(needs_user_clarification=True),
                cached=True,
                needs_refresh=True,
            )
        live = await service.addresses()
        return cached_address_list(
            await repository.save(actor.id, live, ZEPTO_PROVIDER)
        )
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.put("/addresses/selected", response_model=AddressList)
async def select_address(
    body: SelectedAddressRequest,
    session: DbSessionDependency,
    actor: MemoryActorDependency,
) -> AddressList:
    state = await ProviderAddressRepository(session).select(
        actor.id, body.address_id.strip(), ZEPTO_PROVIDER
    )
    if state is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "That Zepto address is not in the saved snapshot. Refresh and try again.",
        )
    return cached_address_list(state)


@router.post("/search", response_model=ProductSearchResult)
async def search(
    body: SearchRequest, service: ZeptoServiceDependency
) -> ProductSearchResult:
    if non_search_reason(body.query) is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "That is a chat message, not a product query. Ask for a product sold by the connected store.",
        )
    try:
        return await service.search(body.query, body.address_id, body.page_number)
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")
