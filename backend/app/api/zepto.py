from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, field_validator

from app.auth import (
    OwnerIfPrivateDependency,
    RefuseIfPrivateDependency,
    UserIdDependency,
)
from app.commerce.demo import DemoZeptoService
from app.commerce.provider_state import (
    ProviderAddressRepository,
    cached_address_list,
    is_demo_address_state,
)
from app.commerce.query_guard import non_search_reason
from app.commerce.swiggy.models import (
    AddressList,
    AddressResolution,
    Pagination,
    ProductSearchResult,
)
from app.commerce.zepto.cart import ZeptoCart
from app.commerce.zepto.errors import (
    ZeptoAddressInvalid,
    ZeptoAuthenticationRequired,
    ZeptoConfigurationError,
    ZeptoProviderError,
    ZeptoRateLimited,
    ZeptoSchemaError,
)
from app.commerce.zepto.history import ZeptoOrderDetail, ZeptoOrderHistory
from app.commerce.zepto.models import (
    CapabilitySummary,
    ConnectionStatus,
    ToolDescription,
)
from app.core.config import get_settings
from app.inbound_delivery import InboundDeliveryServiceDependency
from app.commerce.zepto.address_refresh import PICKER_MAX_AGE_SECONDS
from app.dependencies import (
    BasketServiceDependency,
    ZeptoAddressRefresherDependency,
    ZeptoServiceDependency,
)
from app.memory.dependencies import DbSessionDependency, MemoryActorDependency
from app.memory.errors import MemoryPermissionError
from app.memory.purchase_graph import PurchaseGraphService
from app.oauth_readiness import local_callback_is_ready
from app.shopping.models import BasketLine, ProductRef, rupees_to_paise
from app.shopping.offer_suggestions import (
    LivePurchaseCandidate,
    OfferSuggestion,
    ThresholdOffer,
    suggest_threshold_additions,
)

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


class AddAddressRequest(BaseModel):
    type: Literal["HOME", "WORK", "OTHER"]
    name: str = Field(min_length=1, max_length=80)
    flat_details: str = Field(min_length=1, max_length=160)
    building_name: str = Field(min_length=1, max_length=160)
    building_type: Literal[
        "BUILDING_TYPE_SOCIETY",
        "BUILDING_TYPE_HOUSE",
        "BUILDING_TYPE_OTHERS",
        "BUILDING_TYPE_UNSPECIFIED",
    ] = "BUILDING_TYPE_UNSPECIFIED"
    floor: str | None = Field(default=None, max_length=40)
    landmark: str | None = Field(default=None, max_length=160)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    formatted_address: str = Field(min_length=5, max_length=500)
    short_address: str = Field(min_length=2, max_length=200)
    contact_name: str = Field(min_length=2, max_length=100)
    contact_number: str = Field(
        min_length=7,
        max_length=20,
        pattern=r"^[0-9+() -]+$",
    )

    @field_validator(
        "name",
        "flat_details",
        "building_name",
        "formatted_address",
        "short_address",
        "contact_name",
        "contact_number",
    )
    @classmethod
    def strip_required_address_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("floor", "landmark")
    @classmethod
    def strip_optional_address_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    def provider_arguments(self) -> dict[str, str | float]:
        arguments: dict[str, str | float] = {
            "type": self.type,
            "name": self.name,
            "flatDetails": self.flat_details,
            "buildingName": self.building_name,
            "buildingType": self.building_type,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "formattedAddress": self.formatted_address,
            "shortAddress": self.short_address,
            "contactName": self.contact_name,
            "contactNumber": self.contact_number,
        }
        if self.floor:
            arguments["floor"] = self.floor
        if self.landmark:
            arguments["landmark"] = self.landmark
        return arguments


class OfferSuggestionRequest(BaseModel):
    """One explicit request to check a consented usual against a live offer."""

    memory_product_id: UUID
    address_id: str = Field(min_length=1, max_length=256)
    offer_id: Literal["free_delivery"]
    offer_label: str = Field(min_length=1, max_length=500)
    minimum_order_paise: int = Field(gt=0)
    remaining_paise: int = Field(gt=0)
    confirmed_benefit_paise: int = Field(gt=0)

    @field_validator("address_id", "offer_label")
    @classmethod
    def strip_offer_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("remaining_paise")
    @classmethod
    def remaining_must_fit_threshold(cls, value: int, info: object) -> int:
        data = getattr(info, "data", {})
        minimum = data.get("minimum_order_paise")
        if isinstance(minimum, int) and value > minimum:
            raise ValueError("cannot exceed minimum_order_paise")
        return value


def _raise_http(exc: Exception) -> None:
    if isinstance(exc, ZeptoAuthenticationRequired):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    if isinstance(exc, ZeptoConfigurationError):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    if isinstance(exc, ZeptoRateLimited):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc
    if isinstance(exc, ZeptoAddressInvalid):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Your Zepto saved address changed. Choose a delivery address again.",
        ) from exc
    if isinstance(exc, (ZeptoSchemaError, ZeptoProviderError)):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    raise exc


@router.get("/connection", response_model=ConnectionStatus)
async def connection(
    _user: UserIdDependency,service: ZeptoServiceDependency) -> ConnectionStatus:
    try:
        return await service.connection_status()
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


async def _authorization_url(service: ZeptoServiceDependency) -> str:
    """Check the callback listener (AGENTS.md), then create OAuth state and the URL."""

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
        return await service.authorization_url()
    except HTTPException:
        raise
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.get(
    "/oauth/start",
    response_class=RedirectResponse,
    dependencies=[RefuseIfPrivateDependency],
)
async def oauth_start(service: ZeptoServiceDependency) -> RedirectResponse:
    """Local development only; a private deployment uses POST /oauth/authorization."""

    return RedirectResponse(await _authorization_url(service), status_code=302)


@router.post("/oauth/authorization")
async def oauth_authorization(
    _user: UserIdDependency, service: ZeptoServiceDependency
) -> dict[str, str]:
    """Signed-in replacement for the start navigation: the browser then opens the URL."""

    return {"authorization_url": await _authorization_url(service)}


@router.get("/oauth/readiness", dependencies=[OwnerIfPrivateDependency])
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
async def tools(
    _user: UserIdDependency,service: ZeptoServiceDependency) -> list[ToolDescription]:
    try:
        return await service.list_tools()
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.get("/capabilities", response_model=CapabilitySummary)
async def capabilities(
    _user: UserIdDependency,service: ZeptoServiceDependency) -> CapabilitySummary:
    """Authenticated discovery plus the app's narrow guarded capabilities."""

    try:
        return await service.capabilities()
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.get("/cart", response_model=ZeptoCart)
async def cart(
    _user: UserIdDependency,service: ZeptoServiceDependency) -> ZeptoCart:
    """Return a display-only provider-cart snapshot; never mutate it."""

    try:
        return await service.cart()
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.get("/orders", response_model=ZeptoOrderHistory)
async def order_history(
    _user: UserIdDependency,
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
    _user: UserIdDependency,
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
    refresher: ZeptoAddressRefresherDependency,
    session: DbSessionDependency,
    actor: MemoryActorDependency,
    user_id: UserIdDependency,
    refresh: Annotated[bool, Query()] = False,
    # Bounded automatic refresh: "load" (app load, 30 min default) or "picker" (5 min).
    auto: Annotated[Literal["load", "picker"] | None, Query()] = None,
) -> AddressList:
    try:
        if isinstance(service, DemoZeptoService):
            return await service.addresses()
        repository = ProviderAddressRepository(session)
        state = await repository.get(actor.id, ZEPTO_PROVIDER)
        if state is not None and is_demo_address_state(state):
            await repository.clear(actor.id, ZEPTO_PROVIDER)
            state = None
        if refresh:
            live = await service.addresses()
            if refresher is not None:
                return (await refresher.apply(user_id, live)).addresses
            return cached_address_list(
                await repository.save(actor.id, live, ZEPTO_PROVIDER)
            )
        if state is not None and auto is not None and refresher is not None:
            outcome = await refresher.refresh_if_stale(
                user_id,
                max_age_seconds=PICKER_MAX_AGE_SECONDS if auto == "picker" else None,
                reason=f"auto_{auto}",
            )
            if outcome is not None:
                return outcome.addresses
        if state is not None:
            return cached_address_list(state)
        return AddressList(
            addresses=[],
            pagination=Pagination(),
            resolution=AddressResolution(needs_user_clarification=True),
            cached=True,
            needs_refresh=True,
        )
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.put("/addresses/selected", response_model=AddressList)
async def select_address(
    body: SelectedAddressRequest,
    session: DbSessionDependency,
    actor: MemoryActorDependency,
    user_id: UserIdDependency,
    delivery_line: InboundDeliveryServiceDependency,
    service: ZeptoServiceDependency,
) -> AddressList:
    state = await ProviderAddressRepository(session).select(
        actor.id, body.address_id.strip(), ZEPTO_PROVIDER
    )
    if state is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "That Zepto address is not in the saved snapshot. Refresh and try again.",
        )
    if not isinstance(service, DemoZeptoService):
        service.address_selected()
    # Details shared for the previous address are cleared from the delivery line.
    await delivery_line.address_selection_changed(user_id, "zepto", state.selected_address_id or "")
    return cached_address_list(state)


@router.post("/addresses", response_model=AddressList)
async def add_address(
    body: AddAddressRequest,
    service: ZeptoServiceDependency,
    session: DbSessionDependency,
    actor: MemoryActorDependency,
) -> AddressList:
    """Add an explicitly submitted address to the connected Zepto account."""

    if isinstance(service, DemoZeptoService):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Adding a Zepto address is available only in live mode.",
        )
    repository = ProviderAddressRepository(session)
    previous = await repository.get(actor.id, ZEPTO_PROVIDER)
    old_ids = (
        {address.id for address in cached_address_list(previous).addresses}
        if previous is not None
        else set()
    )
    try:
        live = await service.add_address(body.provider_arguments())
        state = await repository.save(actor.id, live, ZEPTO_PROVIDER)
        new_ids = {address.id for address in live.addresses} - old_ids
        if len(new_ids) == 1:
            selected = await repository.select(
                actor.id, next(iter(new_ids)), ZEPTO_PROVIDER
            )
            if selected is not None:
                state = selected
        return cached_address_list(state)
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.post("/search", response_model=ProductSearchResult)
async def search(
    _user: UserIdDependency,
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


@router.post("/offer-suggestions", response_model=OfferSuggestion | None)
async def offer_suggestion(
    body: OfferSuggestionRequest,
    service: ZeptoServiceDependency,
    baskets: BasketServiceDependency,
    session: DbSessionDependency,
    actor: MemoryActorDependency,
    user_id: UserIdDependency,
) -> OfferSuggestion | None:
    """Resolve one user-selected usual to an exact live Zepto variant.

    This is read-only advice. It never adds the product or changes the provider
    cart; a later explicit basket action still forces a fresh provider quote.
    """

    try:
        remembered = next(
            (
                item
                for item in await PurchaseGraphService(session).suggestions(
                    actor, limit=10
                )
                if item.product_id == body.memory_product_id
                and item.provider == "zepto"
                and item.provider_item_id
            ),
            None,
        )
        if remembered is None:
            return None

        results = await service.search(remembered.product_name, body.address_id, 0)
        product = next(
            (
                item
                for item in results.products
                if item.available and item.product_id == remembered.provider_item_id
            ),
            None,
        )
        if product is None:
            return None

        line = BasketLine(
            ref=ProductRef(
                provider="zepto",
                product_id=product.product_id,
                variant_id=product.spin_id,
                sku_id=product.sku_id,
            ),
            name=product.name,
            brand=product.brand,
            pack_size=product.pack_size,
            unit_price_paise=rupees_to_paise(product.price.offer_price),
            mrp_paise=rupees_to_paise(product.price.mrp),
            quantity=1,
            available=True,
        )
        current_total = body.minimum_order_paise - body.remaining_paise
        suggestions = suggest_threshold_additions(
            await baskets.active(user_id, "zepto"),
            current_qualifying_total_paise=current_total,
            offer=ThresholdOffer(
                id=body.offer_id,
                label=body.offer_label,
                minimum_qualifying_total_paise=body.minimum_order_paise,
                confirmed_benefit_paise=body.confirmed_benefit_paise,
                is_actionable_for_selected_payment=True,
            ),
            candidates=[
                LivePurchaseCandidate(
                    historical_ref=line.ref,
                    live_line=line,
                    last_purchased_at=remembered.last_ordered_at,
                    # Reaching this endpoint requires the person to tap this
                    # exact remembered item; history alone is never enough.
                    explicitly_useful=True,
                )
            ],
            now=datetime.now(UTC),
        )
        return suggestions[0] if suggestions else None
    except MemoryPermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")
