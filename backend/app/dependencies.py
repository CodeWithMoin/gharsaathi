import asyncio
import logging
from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.commerce.provider_state import SavedAddressBook, SavedAddressDirectory
from app.commerce.swiggy.adapter import SwiggyInstamartAdapter
from app.commerce.jev import JevProductReranker
from app.commerce.swiggy.client import SwiggyMcpClient
from app.commerce.demo import (
    DemoAddressBook,
    DemoQuoteProvider,
    DemoSwiggyService,
    DemoZeptoQuoteProvider,
    DemoZeptoService,
)
from app.commerce.swiggy.oauth import SwiggyOAuthClient
from app.commerce.swiggy.quote_provider import SwiggyQuoteProvider
from app.commerce.swiggy.service import SwiggyService
from app.commerce.swiggy.store import EncryptedConnectionStore
from app.commerce.zepto.address_refresh import ZeptoAddressRefresher
from app.commerce.zepto.client import ZeptoMcpClient
from app.commerce.zepto.oauth import ZeptoOAuthClient
from app.commerce.zepto.service import ZeptoService
from app.commerce.zepto.store import EncryptedZeptoConnectionStore
from app.core.config import get_settings
from app.core.local_secrets import get_or_create_local_encryption_key
from app.shopping.approval import ApprovalService
from app.shopping.basket import BasketService
from app.shopping.saved_lists import OrderedBasketReader, OrderedBaskets, SavedListStore
from app.shopping.database import DatabaseApprovalRepository, DatabaseBasketRepository
from app.shopping.checkout import (
    CheckoutService,
    DatabaseCheckoutAttemptRepository,
    ZeptoCodGateway,
)
from app.database.session import get_session_factory
from app.inbound_delivery import get_inbound_delivery_service
from app.inbound_delivery.service import LineClearingAddressBook
from app.shopping.order_confirmation import OrderConfirmations
from app.shopping.reconciliation import CheckoutReconciler
from app.shopping.payments import PrepaidGateway
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
from app.conversations.context import ConversationContextLoader
from app.conversations.summarizer import (
    BackgroundConversationSummaries,
    ConversationSummaries,
    DisabledConversationSummaries,
    OpenAIConversationSummarizer,
)
from app.memory.recall import DatabasePreferenceRecaller
from app.photos.extraction import OpenAIPhotoExtractor, PhotoReader
from app.photos.store import PhotoAttachmentStore
from app.photos.visual_match import HttpImageFetcher, OpenAIVisualMatchModel, PhotoVisualMatcher
from app.user_profile import DatabaseProfileReader
from app.chat.service import ChatTurnService
from app.voice.service import LiveSessionService
from app.voice.sideband import SidebandHub
from app.notifications.sender import VapidConfig, WebPushSender
from app.notifications.service import PushEvents, PushNotifier
from app.notifications.store import PushStore
from app.notifications.vapid import public_key_for

logger = logging.getLogger(__name__)


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
    if get_voice_service.cache_info().currsize:
        await get_voice_service().aclose()
    if get_push_events.cache_info().currsize:
        await get_push_events().aclose()


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
def get_zepto_address_refresher() -> ZeptoAddressRefresher | None:
    """Bounded automatic Zepto address refresh; None in demo mode (no provider calls)."""

    settings = get_settings()
    if settings.commerce_mode == "demo":
        return None
    service = get_live_zepto_service()
    refresher = ZeptoAddressRefresher(
        service,
        get_session_factory(),
        get_basket_service(),
        max_age_seconds=settings.zepto_address_refresh_max_age_min * 60,
    )
    service.on_address_removed = refresher.address_missing
    return refresher


ZeptoAddressRefresherDependency = Annotated[
    ZeptoAddressRefresher | None, Depends(get_zepto_address_refresher)
]


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
def get_conversation_context_loader() -> ConversationContextLoader:
    """Shared by voice session start and typed chat turns."""

    return ConversationContextLoader(
        get_session_factory(), get_basket_repository(), get_approval_repository()
    )


@lru_cache
def get_conversation_summaries() -> ConversationSummaries:
    settings = get_settings()
    api_key = settings.openai_api_key.get_secret_value().strip()
    if not api_key:
        return DisabledConversationSummaries()
    return BackgroundConversationSummaries(
        get_session_factory(),
        OpenAIConversationSummarizer(
            api_key, settings.openai_base_url, settings.conversation_summary_model
        ),
    )


ConversationSummariesDependency = Annotated[
    ConversationSummaries, Depends(get_conversation_summaries)
]


@lru_cache
def get_photo_store() -> PhotoAttachmentStore:
    return PhotoAttachmentStore()


@lru_cache
def get_photo_reader() -> PhotoReader:
    settings = get_settings()
    api_key = settings.openai_api_key.get_secret_value().strip()
    extractor = (
        OpenAIPhotoExtractor(api_key, settings.openai_base_url, settings.photo_extraction_model)
        if api_key
        else None
    )
    matcher = (
        PhotoVisualMatcher(
            OpenAIVisualMatchModel(
                api_key,
                settings.openai_base_url,
                settings.photo_visual_match_model.strip() or settings.photo_extraction_model,
            ),
            HttpImageFetcher(),
        )
        if api_key and settings.photo_visual_match_enabled
        else None
    )
    return PhotoReader(get_photo_store(), extractor, visual_matcher=matcher)


PhotoStoreDependency = Annotated[PhotoAttachmentStore, Depends(get_photo_store)]


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
        preference_recall=DatabasePreferenceRecaller(get_session_factory()),
        profile_reader=DatabaseProfileReader(get_session_factory()),
        addresses=SavedAddressDirectory(get_session_factory()),
        context_reader=get_conversation_context_loader(),
        order_confirmations=get_order_confirmations(),
        address_book=LineClearingAddressBook(
            DemoAddressBook()
            if get_settings().commerce_mode == "demo"
            else SavedAddressBook(get_session_factory()),
            get_inbound_delivery_service(),
        ),
        photo_reader=get_photo_reader(),
        order_tracking=get_order_tracking_service(),
        order_history=get_zepto_service(),
        saved_lists=get_saved_list_store(),
    )


VoiceServiceDependency = Annotated[LiveSessionService, Depends(get_voice_service)]
SidebandHubDependency = Annotated[SidebandHub, Depends(get_sideband_hub)]


@lru_cache
def get_chat_service() -> ChatTurnService:
    return ChatTurnService(get_settings(), get_voice_service())


ChatServiceDependency = Annotated[ChatTurnService, Depends(get_chat_service)]


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


@lru_cache
def get_zepto_payment_gateway() -> PrepaidGateway | None:
    """Prepaid Zepto orders, or None in demo mode (cash on delivery only)."""

    settings = get_settings()
    if settings.commerce_mode == "demo":
        return None
    from app.commerce.zepto.payments import (
        VERIFIED_CONTRACT,
        ZeptoPaymentGateway,
        parse_allowed_methods,
    )
    from app.commerce.zepto.quote_provider import ZeptoQuoteProvider

    client = get_live_zepto_service().client
    checkout_lock = get_zepto_checkout_lock()
    return ZeptoPaymentGateway(
        client=client,
        quotes=ZeptoQuoteProvider(client, checkout_lock=checkout_lock),
        enabled=settings.zepto_online_payments_enabled,
        allowed=parse_allowed_methods(settings.zepto_payment_methods_allowed),
        contract=VERIFIED_CONTRACT,
        checkout_lock=checkout_lock,
    )


@lru_cache
def get_order_confirmations() -> OrderConfirmations:
    """One registry per process, shared by voice, typed chat and the tap endpoint."""

    return OrderConfirmations(
        get_basket_repository(),
        get_approval_service(),
        payments=get_zepto_payment_gateway(),
        reconciler=get_checkout_reconciler(),
        push=get_push_events(),
    )


@lru_cache
def get_checkout_reconciler() -> CheckoutReconciler:
    """Read-only order-history checks for checkouts whose outcome is unknown."""

    return CheckoutReconciler(
        DatabaseCheckoutAttemptRepository(get_session_factory()),
        get_basket_repository(),
        get_zepto_service(),
        tracking=get_order_tracking_service(),
        push=get_push_events(),
    )


CheckoutReconcilerDependency = Annotated[CheckoutReconciler, Depends(get_checkout_reconciler)]


OrderConfirmationsDependency = Annotated[OrderConfirmations, Depends(get_order_confirmations)]
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
            push=get_push_events(),
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
        payments=get_zepto_payment_gateway(),
        push=get_push_events(),
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
    return OrderTrackingService(
        OrderTrackingStore(settings.tracking_data_path),
        providers,
        push=get_push_events(),
        delivery_line=get_inbound_delivery_service(),
    )


def vapid_config() -> VapidConfig | None:
    """VAPID settings when all three are set and the key pair matches, else None."""

    settings = get_settings()
    public = settings.web_push_vapid_public_key.strip()
    private = settings.web_push_vapid_private_key.get_secret_value().strip()
    subject = settings.web_push_subject.strip()
    if not public or not private or not subject.startswith(("mailto:", "https://")):
        return None
    if public_key_for(private) != public.rstrip("="):
        logger.warning("event=web_push_disabled reason=vapid_key_mismatch")
        return None
    return VapidConfig(public_key=public, private_key=private, subject=subject)


@lru_cache
def get_push_notifier() -> PushNotifier:
    """Browser notifications; disabled (feature hidden) without valid VAPID keys."""

    config = vapid_config()
    if config is None:
        return PushNotifier(None, None)
    return PushNotifier(
        PushStore(get_session_factory(), get_provider_encryption_key()),
        WebPushSender(config),
        public_key=config.public_key,
    )


@lru_cache
def get_push_events() -> PushEvents:
    return PushEvents(
        get_push_notifier(),
        baskets=get_basket_repository(),
        online_payments_enabled=get_settings().zepto_online_payments_enabled,
    )


PushNotifierDependency = Annotated[PushNotifier, Depends(get_push_notifier)]


@lru_cache
def get_saved_list_store() -> SavedListStore:
    return SavedListStore(get_session_factory())


@lru_cache
def get_ordered_baskets() -> OrderedBaskets:
    return OrderedBaskets(get_session_factory())


SavedListStoreDependency = Annotated[SavedListStore, Depends(get_saved_list_store)]
OrderedBasketsDependency = Annotated[OrderedBasketReader, Depends(get_ordered_baskets)]
