from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, field_validator

from app.commerce.swiggy.errors import (
    SwiggyAuthenticationRequired,
    SwiggyConfigurationError,
    SwiggyProviderError,
    SwiggyRateLimited,
    SwiggySchemaError,
)
from app.commerce.query_guard import non_search_reason
from app.commerce.swiggy.models import (
    AddressList,
    AddressResolution,
    AppliedMemory,
    ConnectionStatus,
    Pagination,
    PersonalizedProductSearchResult,
    ProductSearchResult,
    ToolDescription,
)
from app.commerce.provider_state import (
    ProviderAddressRepository,
    cached_address_list,
    is_demo_address_state,
)
from app.commerce.demo import DemoSwiggyService
from app.core.config import get_settings
from app.dependencies import SwiggyServiceDependency
from app.memory.dependencies import DbSessionDependency, MemoryActorDependency
from app.memory.personalization import excludes_product, personalize_search_query
from app.memory.schemas import MemoryRetrieveRequest
from app.memory.service import MemoryService
from app.oauth_readiness import local_callback_is_ready


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


class SelectedAddressRequest(BaseModel):
    address_id: str = Field(min_length=1, max_length=256)

    @field_validator("address_id")
    @classmethod
    def strip_address_id(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


def _raise_http(exc: Exception) -> None:
    if isinstance(exc, SwiggyAuthenticationRequired):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    if isinstance(exc, SwiggyConfigurationError):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    if isinstance(exc, SwiggyRateLimited):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            str(exc),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
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
        settings = get_settings()
        if not await local_callback_is_ready(settings.swiggy_oauth_redirect_uri):
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Swiggy's OAuth callback is not reachable. Start the backend before connecting.",
            )
        return RedirectResponse(await service.authorization_url(), status_code=302)
    except HTTPException:
        raise
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.get("/oauth/readiness")
async def oauth_readiness() -> dict[str, bool | str]:
    ready = await local_callback_is_ready(get_settings().swiggy_oauth_redirect_uri)
    return {
        "ready": ready,
        "message": (
            "Swiggy is ready to connect."
            if ready
            else "Swiggy's OAuth callback is unavailable. Start the backend first."
        ),
    }


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
async def disconnect(
    service: SwiggyServiceDependency,
    session: DbSessionDependency,
    actor: MemoryActorDependency,
) -> None:
    try:
        await service.disconnect()
        await ProviderAddressRepository(session).clear(actor.id)
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
    session: DbSessionDependency,
    actor: MemoryActorDependency,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=10)] = 10,
    refresh: Annotated[bool, Query()] = False,
) -> AddressList:
    try:
        # Demo fixtures are request-local. They must never read or write the
        # authenticated provider snapshot used after switching to live mode.
        if isinstance(service, DemoSwiggyService):
            return await service.addresses(page, page_size)
        repository = ProviderAddressRepository(session)
        state = await repository.get(actor.id)
        if state is not None and is_demo_address_state(state):
            # Clean up snapshots written by older versions of the demo route.
            await repository.clear(actor.id)
            state = None
        if state is not None and not refresh:
            return cached_address_list(state)
        if not refresh:
            return AddressList(
                addresses=[],
                pagination=Pagination(page=page, page_size=page_size),
                resolution=AddressResolution(needs_user_clarification=True),
                cached=True,
                needs_refresh=True,
            )
        live = await service.addresses(page, page_size)
        return cached_address_list(await repository.save(actor.id, live))
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.put("/addresses/selected", response_model=AddressList)
async def select_address(
    body: SelectedAddressRequest,
    session: DbSessionDependency,
    actor: MemoryActorDependency,
) -> AddressList:
    state = await ProviderAddressRepository(session).select(actor.id, body.address_id)
    if state is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "That address is not in the saved snapshot. Refresh addresses and try again.",
        )
    return cached_address_list(state)


@router.post("/search", response_model=ProductSearchResult)
async def search(
    body: SearchRequest, service: SwiggyServiceDependency
) -> ProductSearchResult:
    try:
        return await service.search(body.query, body.address_id, body.offset)
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.post("/search/personalized", response_model=PersonalizedProductSearchResult)
async def personalized_search(
    body: SearchRequest,
    service: SwiggyServiceDependency,
    session: DbSessionDependency,
    actor: MemoryActorDependency,
) -> PersonalizedProductSearchResult:
    if non_search_reason(body.query) is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "That is a chat message, not a product query. Ask for a grocery or household item.",
        )
    try:
        memories = await MemoryService(session).retrieve(
            actor,
            MemoryRetrieveRequest(
                purpose="personalize_search",
                memory_types=[
                    "brand_preference",
                    "pack_size_preference",
                    "quantity_preference",
                    "dietary_preference",
                    "product_avoidance",
                ],
            ),
        )
        personalization = personalize_search_query(body.query, memories)
        result = await service.search(
            personalization.provider_query,
            body.address_id,
            body.offset,
        )
        products = [
            product
            for product in result.products
            if not excludes_product(product, personalization.avoid_terms)
        ]
        similar_products = [
            product
            for product in result.similar_products
            if not excludes_product(product, personalization.avoid_terms)
        ]
        applied_by_id = {
            memory.id: AppliedMemory(
                id=memory.id,
                memory_type=memory.memory_type,
                text=memory.text,
            )
            for memory in memories
            if memory.id in personalization.applied_memory_ids
        }
        excluded_count = (
            len(result.products)
            + len(result.similar_products)
            - len(products)
            - len(similar_products)
        )
        return PersonalizedProductSearchResult(
            products=products,
            similar_products=similar_products,
            next_offset=result.next_offset,
            original_query=body.query,
            provider_query=personalization.provider_query,
            applied_memories=list(applied_by_id.values()),
            excluded_product_count=excluded_count,
        )
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")
