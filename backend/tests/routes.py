"""Route-table helpers shared by the auth and routing guard tests."""

import re
from collections.abc import Iterator

from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.auth import require_user_id

# Routes reachable without a Clerk session, each with the reason it is safe.
PUBLIC_ROUTES: dict[str, str] = {
    "GET /health": "liveness probe",
    "GET /api/swiggy/oauth/start": "browser navigation into provider OAuth; cannot carry a bearer",
    "GET /api/swiggy/oauth/readiness": "reports callback reachability only",
    "GET /api/swiggy/oauth/callback": "provider redirect; validated by OAuth state",
    "GET /api/zepto/oauth/start": "browser navigation into provider OAuth; cannot carry a bearer",
    "GET /api/zepto/oauth/readiness": "reports callback reachability only",
    "GET /api/zepto/oauth/callback": "provider redirect; validated by OAuth state",
    "GET /api/support/agent/tools": "static tool definitions, no user data",
    # EventSource cannot send Authorization headers. The stream requires the
    # per-session token issued to the creating (Clerk-verified) user by
    # POST /api/voice/session, plus an Origin check.
    "GET /api/voice/sessions/{session_id}/events": "bound to its creator by a per-session events token",
    # Vobiz webhooks (rider assist): HMAC-SHA256 signature over the URL + nonce with
    # the Vobiz auth token, checked fail-closed; off unless RIDER_ASSIST_ENABLED.
    "POST /api/rider/vobiz/answer": "Vobiz answer URL; Vobiz webhook signature (V3/V2) required",
    "POST /api/rider/vobiz/after-stream": "Vobiz redirect after the stream; Vobiz webhook signature required",
    # Sarvam agent API tool: SARVAM_TOOL_TOKEN (constant-time compare); 404 unless
    # SARVAM_LIVE_CONTEXT_ENABLED; answers only for SARVAM_INBOUND_OWNER_USER_ID.
    "POST /api/sarvam/delivery-context": "Sarvam API tool; static tool token required, off by default",
}

# Local dev inspector (app/api/dev.py): each needs a verified user, but answers 404
# instead of 401/403 unless DEV_TOOLS_ENABLED, loopback, unproxied and not private.
DEV_ROUTES: dict[str, str] = {
    "GET /api/dev/status": "inspector availability",
    "GET /api/dev/traces": "local DEV_TRACE records",
    "GET /api/dev/state": "the signed-in user's baskets, memory, watches, notifications",
    "POST /api/dev/basket/clear": "owner's own basket clear",
    "POST /api/dev/doctor": "read-only doctor",
    "POST /api/dev/reconcile-dry-run": "read-only, budgeted Zepto history check",
}


def walk(routes, prefix: str = "") -> Iterator[tuple[str, APIRoute]]:
    """Yield (full_path, route) in match order.

    FastAPI >=0.165 keeps included routers lazily (``original_router`` +
    ``include_context``); older versions flatten them into ``APIRoute``.
    """

    for route in routes:
        if isinstance(route, APIRoute):
            yield prefix + route.path, route
        elif hasattr(route, "original_router") and hasattr(route, "include_context"):
            yield from walk(
                route.original_router.routes, prefix + (route.include_context.prefix or "")
            )


def operations(app: FastAPI) -> list[tuple[str, str, APIRoute]]:
    """Every (method, full_path, route), in the order Starlette tries them."""

    return [
        (method, path, route)
        for path, route in walk(app.routes)
        for method in sorted(route.methods or ())
        if method != "HEAD"
    ]


def requires_user(route: APIRoute) -> bool:
    pending = list(route.dependant.dependencies)
    while pending:
        dependency = pending.pop()
        if dependency.call is require_user_id:
            return True
        pending.extend(dependency.dependencies)
    return False


def concrete_path(path: str, value: str = "00000000-0000-4000-8000-000000000000") -> str:
    """Fill every ``{param}`` with a value that also satisfies UUID path types."""

    return re.sub(r"\{[^}]+\}", value, path)
