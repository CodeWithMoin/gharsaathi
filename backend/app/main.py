from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.swiggy import router as swiggy_router
from app.api.swiggy_orders import router as swiggy_orders_router
from app.api.zepto import router as zepto_router
from app.api.memory import router as memory_router
from app.api.purchase_memory import router as purchase_memory_router
from app.core.config import get_settings
from app.core.logging import install_sensitive_log_filters

settings = get_settings()
install_sensitive_log_filters()
app = FastAPI(title="Household Shopping Agent API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-User-Id"],
    expose_headers=["Retry-After"],
)
app.include_router(
    purchase_memory_router,
    prefix="/api/memory",
    tags=["Purchase Memory"],
)
app.include_router(memory_router, prefix="/api/memory", tags=["Memory"])
app.include_router(swiggy_router, prefix="/api/swiggy", tags=["Swiggy Instamart"])
app.include_router(
    swiggy_orders_router,
    prefix="/api/swiggy/orders",
    tags=["Swiggy Instamart History"],
)
app.include_router(zepto_router, prefix="/api/zepto", tags=["Zepto"])


@app.get("/health", tags=["System"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
