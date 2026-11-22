from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.api.basket import baskets_router
from app.api.basket import router as basket_router
from app.api.lists import router as lists_router
from app.api.quick_actions import router as quick_actions_router
from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.api.dev import router as dev_router
from app.api.notifications import router as notifications_router
from app.api.orders import router as orders_router
from app.api.swiggy import router as swiggy_router
from app.api.support import router as support_router
from app.api.tracking import router as tracking_router
from app.api.zepto import router as zepto_router
from app.api.zepto_checkout import router as zepto_checkout_router
from app.api.memory import router as memory_router
from app.api.legal_consent import router as legal_consent_router
from app.api.profile import router as profile_router
from app.api.purchase_memory import router as purchase_memory_router
from app.api.rider import router as rider_router
from app.api.inbound_delivery import router as inbound_delivery_router
from app.api.voice import router as voice_router
from app.auth import PrivateDeploymentForbidden
from app.inbound_delivery import InboundDeliveryError
from app.core.config import get_settings
from app.core.logging import install_sensitive_log_filters
from app.legal_consent import LegalConsentRequired
from app.dependencies import (
    close_cached_services,
    get_order_tracking_service,
)
from app.tracking.service import OrderTrackingPoller

settings = get_settings()
install_sensitive_log_filters()


@asynccontextmanager
async def lifespan(application: FastAPI):
    tracking_service = get_order_tracking_service()
    # A poller owns asyncio primitives and therefore belongs to this exact
    # application lifespan/event loop. The persisted service can stay cached.
    tracking_poller = OrderTrackingPoller(tracking_service)
    application.state.order_tracking_service = tracking_service
    if settings.tracking_polling_enabled:
        tracking_poller.start()
    try:
        yield
    finally:
        if settings.tracking_polling_enabled:
            await tracking_poller.aclose()
        await close_cached_services()


# A private deployment does not publish its API schema or docs.
_docs = {} if not settings.private_deployment else {
    "docs_url": None,
    "redoc_url": None,
    "openapi_url": None,
}
app = FastAPI(
    title="Household Shopping Agent API",
    version="0.2.0",
    lifespan=lifespan,
    **_docs,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    expose_headers=["Retry-After"],
)


@app.exception_handler(LegalConsentRequired)
async def legal_consent_required(_: Request, exc: LegalConsentRequired) -> JSONResponse:
    # `detail` stays a string for existing clients; `code` is machine-readable.
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={"detail": exc.message, "code": exc.code},
    )


@app.exception_handler(PrivateDeploymentForbidden)
async def private_deployment_forbidden(
    _: Request, exc: PrivateDeploymentForbidden
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={"detail": exc.message, "code": exc.code},
    )


@app.exception_handler(InboundDeliveryError)
async def inbound_delivery_error(_: Request, exc: InboundDeliveryError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message, "code": exc.code},
    )


# Purchase memory must be mounted first: memory's `DELETE /{memory_id}` would
# otherwise capture `DELETE /purchase-history` and reject it as a bad UUID.
app.include_router(
    purchase_memory_router,
    prefix="/api/memory",
    tags=["Purchase Memory"],
)
app.include_router(memory_router, prefix="/api/memory", tags=["Memory"])
app.include_router(legal_consent_router, prefix="/api/legal-consent", tags=["Legal consent"])
app.include_router(profile_router, prefix="/api/profile", tags=["Profile"])
app.include_router(swiggy_router, prefix="/api/swiggy", tags=["Swiggy Instamart"])
app.include_router(zepto_router, prefix="/api/zepto", tags=["Zepto"])
app.include_router(
    zepto_checkout_router,
    prefix="/api/zepto/checkout",
    tags=["Zepto Checkout"],
)
app.include_router(voice_router, prefix="/api/voice", tags=["Voice"])
app.include_router(chat_router, prefix="/api/chat", tags=["Typed chat"])
app.include_router(
    inbound_delivery_router,
    prefix="/api/inbound-delivery",
    tags=["Inbound Delivery"],
)
app.include_router(basket_router, prefix="/api/basket", tags=["Basket"])
app.include_router(baskets_router, prefix="/api/baskets", tags=["Basket"])
app.include_router(lists_router, prefix="/api/lists", tags=["Saved lists"])
app.include_router(quick_actions_router, prefix="/api/quick-actions", tags=["Quick actions"])
app.include_router(orders_router, prefix="/api/orders", tags=["Orders"])
app.include_router(support_router, prefix="/api/support", tags=["Customer Support"])
app.include_router(tracking_router, prefix="/api/tracking", tags=["Order Tracking"])
app.include_router(rider_router, prefix="/api/rider", tags=["Rider assist"])
app.include_router(
    notifications_router, prefix="/api/notifications", tags=["Browser notifications"]
)
app.include_router(
    conversations_router,
    prefix="/api/conversations",
    tags=["Conversations"],
)
# Local dev inspector: 404 unless DEV_TOOLS_ENABLED, loopback and signed in (app/api/dev.py).
app.include_router(dev_router, prefix="/api/dev", tags=["Dev tools"], include_in_schema=False)


@app.get("/health", tags=["System"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
