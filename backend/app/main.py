from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.basket import router as basket_router
from app.api.conversations import router as conversations_router
from app.api.swiggy import router as swiggy_router
from app.api.support import router as support_router
from app.api.tracking import router as tracking_router
from app.api.zepto import router as zepto_router
from app.api.zepto_checkout import router as zepto_checkout_router
from app.api.memory import router as memory_router
from app.api.purchase_memory import router as purchase_memory_router
from app.api.voice import router as voice_router
from app.core.config import get_settings
from app.core.logging import install_sensitive_log_filters
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


app = FastAPI(title="Household Shopping Agent API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    expose_headers=["Retry-After"],
)
app.include_router(memory_router, prefix="/api/memory", tags=["Memory"])
app.include_router(
    purchase_memory_router,
    prefix="/api/memory",
    tags=["Purchase Memory"],
)
app.include_router(swiggy_router, prefix="/api/swiggy", tags=["Swiggy Instamart"])
app.include_router(zepto_router, prefix="/api/zepto", tags=["Zepto"])
app.include_router(
    zepto_checkout_router,
    prefix="/api/zepto/checkout",
    tags=["Zepto Checkout"],
)
app.include_router(voice_router, prefix="/api/voice", tags=["Voice"])
app.include_router(basket_router, prefix="/api/basket", tags=["Basket"])
app.include_router(support_router, prefix="/api/support", tags=["Customer Support"])
app.include_router(tracking_router, prefix="/api/tracking", tags=["Order Tracking"])
app.include_router(
    conversations_router,
    prefix="/api/conversations",
    tags=["Conversations"],
)


@app.get("/health", tags=["System"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
