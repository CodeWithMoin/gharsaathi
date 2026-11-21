import asyncio
from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.commerce.provider_state import SavedAddressDirectory
from app.commerce.swiggy.adapter import SwiggyInstamartAdapter
from app.commerce.jev import JevProductReranker
from app.commerce.swiggy.client import SwiggyMcpClient
from app.commerce.demo import (
    DemoQuoteProvider,
    DemoSwiggyService,
    DemoZeptoQuoteProvider,
    DemoZeptoService,
)
from app.commerce.swiggy.oauth import SwiggyOAuthClient
from app.commerce.swiggy.quote_provider import SwiggyQuoteProvider
from app.commerce.swiggy.service import SwiggyService
from app.commerce.swiggy.store import EncryptedConnectionStore
from app.commerce.zepto.client import ZeptoMcpClient
from app.commerce.zepto.oauth import ZeptoOAuthClient
from app.commerce.zepto.service import ZeptoService
from app.commerce.zepto.store import EncryptedZeptoConnectionStore
from app.core.config import get_settings
from app.core.local_secrets import get_or_create_local_encryption_key
from app.shopping.approval import ApprovalService
from app.shopping.basket import BasketService
from app.shopping.database import DatabaseApprovalRepository, DatabaseBasketRepository
from app.shopping.checkout import (
    CheckoutService,
    DatabaseCheckoutAttemptRepository,
    ZeptoCodGateway,
)
from app.database.session import get_session_factory
from app.shopping.errors import ProviderUnavailable
from app.shopping.models import Basket, ProviderName, Quote
from app.shopping.ports import QuoteProvider
from app.shopping.providers import ProviderRegistry, get_provider_registry
from app.support.service import SupportService
from app.support.store import ComplaintStore
from app.tracking.adapters import (
    DemoOrderStatusAdapter,
    SwiggyDeliveryStatusAdapter,
    ZeptoOrderStatusAdapter,
)
from app.tracking.models import TrackingProvider
from app.tracking.service import OrderTrackingService
from app.tracking.store import OrderTrackingStore
from app.voice.service import LiveSessionService
from app.voice.sideband import SidebandHub


@lru_cache
def get_provider_encryption_key() -> str:
    settings = get_settings()
    if settings.app_encryption_key.strip():
        return settings.app_encryption_key
    return get_or_create_local_encryption_key(settings.local_encryption_key_path)


@lru_cache
def get_live_swiggy_service() -> SwiggyService:
    settings = get_settings()
    store = EncryptedConnectionStore(
        settings.swiggy_token_store_path, get_provider_encryption_key()
    )
    oauth = SwiggyOAuthClient(settings, store)
    client = SwiggyMcpClient(settings, oauth)
    adapter = SwiggyInstamartAdapter(client)
    return SwiggyService(oauth, client, adapter, get_product_reranker())


@lru_cache
def get_product_reranker() -> JevProductReranker | None:
    settings = get_settings()
    if (
        not settings.jev_reranking_enabled
        or not settings.jev_api_key.get_secret_value().strip()
    ):
        return None
    return JevProductReranker(
        api_key=settings.jev_api_key,
        base_url=settings.jev_base_url,
        model=settings.jev_model,
        timeout_seconds=settings.jev_timeout_seconds,
    )


@lru_cache
def get_demo_swiggy_service() -> DemoSwiggyService:
    return DemoSwiggyService()


@lru_cache
def get_swiggy_service() -> SwiggyService | DemoSwiggyService:
    if get_settings().commerce_mode == "demo":
        return get_demo_swiggy_service()
    return get_live_swiggy_service()


async def close_cached_services() -> None:
    """Release provider transports without creating an unused cached service."""

    if get_live_swiggy_service.cache_info().currsize:
        await get_live_swiggy_service().client.aclose()
    if get_live_zepto_service.cache_info().currsize:
        await get_live_zepto_service().client.aclose()


SwiggyServiceDependency = Annotated[
    SwiggyService | DemoSwiggyService, Depends(get_swiggy_service)
]


@lru_cache
def get_swiggy_quote_provider() -> SwiggyQuoteProvider | DemoQuoteProvider:
    if get_settings().commerce_mode == "demo":
        return DemoQuoteProvider()
    return SwiggyQuoteProvider(get_live_swiggy_service().client)


@lru_cache
def get_quote_provider_registry() -> ProviderRegistry:
    registry = get_provider_registry()
    registry.register(get_swiggy_quote_provider())
    if get_settings().commerce_mode == "demo":
        registry.register(DemoZeptoQuoteProvider())
    else:
        registry.register(get_zepto_quote_provider())
    return registry


def get_live_quote_provider(name: ProviderName) -> QuoteProvider:
    """Return only verified, non-checkout pricing capabilities."""
    return get_quote_provider_registry().get(name)


@lru_cache
def get_live_zepto_service() -> ZeptoService:
    settings = get_settings()
    store = EncryptedZeptoConnectionStore(
        settings.zepto_token_store_path, get_provider_encryption_key()
    )
    oauth = ZeptoOAuthClient(settings, store)
    client = ZeptoMcpClient(settings, oauth)
    return ZeptoService(oauth, client, get_product_reranker())


@lru_cache
def get_demo_zepto_service() -> DemoZeptoService:
    return DemoZeptoService()


@lru_cache
def get_zepto_service() -> ZeptoService | DemoZeptoService:
    if get_settings().commerce_mode == "demo":
        return get_demo_zepto_service()
    return get_live_zepto_service()


ZeptoServiceDependency = Annotated[
    ZeptoService | DemoZeptoService, Depends(get_zepto_service)
]


@lru_cache
def get_zepto_checkout_lock() -> asyncio.Lock:
    """Serialize the provider cart preview through final confirmation."""

    return asyncio.Lock()


@lru_cache
def get_zepto_quote_provider() -> QuoteProvider:
    from app.commerce.zepto.quote_provider import ZeptoQuoteProvider

    return ZeptoQuoteProvider(
        get_live_zepto_service().client,
        checkout_lock=get_zepto_checkout_lock(),
    )


@lru_cache
def get_sideband_hub() -> SidebandHub:
    return SidebandHub(get_settings())


@lru_cache
def get_voice_service() -> LiveSessionService:
    return LiveSessionService(
        get_settings(),
        get_swiggy_service(),
        get_sideband_hub(),
        baskets=get_basket_service(),
        approvals=get_approval_service(),
        zepto_searcher=get_zepto_service(),
        zepto_connection=get_zepto_service(),
        addresses=SavedAddressDirectory(get_session_factory()),
    )


VoiceServiceDependency = Annotated[LiveSessionService, Depends(get_voice_service)]
SidebandHubDependency = Annotated[SidebandHub, Depends(get_sideband_hub)]


@lru_cache
def get_basket_repository() -> DatabaseBasketRepository:
    return DatabaseBasketRepository(get_session_factory())


@lru_cache
def get_approval_repository() -> DatabaseApprovalRepository:
    return DatabaseApprovalRepository(get_session_factory())


@lru_cache
def get_basket_service() -> BasketService:
    return BasketService(get_basket_repository(), get_approval_repository())


@lru_cache
def get_approval_service() -> ApprovalService:
    return ApprovalService(
        get_basket_repository(),
        get_approval_repository(),
        get_live_quote_provider,
        workflow_lock=get_zepto_checkout_lock(),
    )


BasketServiceDependency = Annotated[BasketService, Depends(get_basket_service)]
ApprovalServiceDependency = Annotated[ApprovalService, Depends(get_approval_service)]


class _DisabledZeptoCheckout:
    async def quote(self, basket: Basket) -> Quote:
        raise ProviderUnavailable(
            "Real Zepto checkout is unavailable while COMMERCE_MODE=demo."
        )

    async def confirm_cod(self, basket: Basket) -> object:
        raise ProviderUnavailable(
            "Real Zepto checkout is unavailable while COMMERCE_MODE=demo."
        )


@lru_cache
def get_zepto_checkout_service() -> CheckoutService:
    attempts = DatabaseCheckoutAttemptRepository(get_session_factory())
    if get_settings().commerce_mode == "demo":
        disabled = _DisabledZeptoCheckout()
        return CheckoutService(
            get_basket_repository(),
            get_approval_repository(),
            attempts,
            disabled,
            disabled,
        )

    # Imported lazily so the non-consequential shopping API remains available
    # if a deployment intentionally omits live checkout configuration.
    from app.commerce.zepto.quote_provider import (
        ZeptoCodCheckoutGateway,
        ZeptoQuoteProvider,
    )

    client = get_live_zepto_service().client
    checkout_lock = get_zepto_checkout_lock()
    quote_provider = get_zepto_quote_provider()
    gateway: ZeptoCodGateway = ZeptoCodCheckoutGateway(
        client, checkout_lock=checkout_lock
    )
    return CheckoutService(
        get_basket_repository(),
        get_approval_repository(),
        attempts,
        quote_provider,
        gateway,
        checkout_lock=checkout_lock,
    )


CheckoutServiceDependency = Annotated[
    CheckoutService, Depends(get_zepto_checkout_service)
]


@lru_cache
def get_support_service() -> SupportService:
    return SupportService(ComplaintStore(get_settings().support_data_path))


SupportServiceDependency = Annotated[SupportService, Depends(get_support_service)]


@lru_cache
def get_order_tracking_service() -> OrderTrackingService:
    settings = get_settings()
    if settings.commerce_mode == "demo":
        providers = {
            TrackingProvider.SWIGGY: DemoOrderStatusAdapter(TrackingProvider.SWIGGY),
            TrackingProvider.ZEPTO: DemoOrderStatusAdapter(TrackingProvider.ZEPTO),
        }
    else:
        providers = {
            TrackingProvider.SWIGGY: SwiggyDeliveryStatusAdapter(
                get_live_swiggy_service().client
            ),
            TrackingProvider.ZEPTO: ZeptoOrderStatusAdapter(
                get_live_zepto_service().client
            ),
        }
    return OrderTrackingService(OrderTrackingStore(settings.tracking_data_path), providers)
