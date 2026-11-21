from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.auth import require_user_id
from app.core.config import get_settings
from app.dependencies import get_swiggy_service, get_zepto_service
from app.main import app

# Browser navigations during OAuth cannot carry a bearer token.
PUBLIC_PROVIDER_ROUTES = {
    "/api/swiggy/oauth/start",
    "/api/swiggy/oauth/readiness",
    "/api/swiggy/oauth/callback",
    "/api/zepto/oauth/start",
    "/api/zepto/oauth/readiness",
    "/api/zepto/oauth/callback",
}


def _walk(routes, prefix=""):
    """Yield (full_path, route); FastAPI may keep included routers lazily."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield prefix + route.path, route
        elif hasattr(route, "original_router") and hasattr(route, "include_context"):
            yield from _walk(
                route.original_router.routes, prefix + (route.include_context.prefix or "")
            )


def _provider_routes() -> list[tuple[str, APIRoute]]:
    return [
        (path, route)
        for path, route in _walk(app.routes)
        if path.startswith(("/api/swiggy", "/api/zepto"))
    ]


def _requires_user(route: APIRoute) -> bool:
    pending = list(route.dependant.dependencies)
    while pending:
        dependency = pending.pop()
        if dependency.call is require_user_id:
            return True
        pending.extend(dependency.dependencies)
    return False


def test_provider_routes_are_discovered() -> None:
    paths = {path for path, _ in _provider_routes()}
    assert len(paths) >= 20
    assert PUBLIC_PROVIDER_ROUTES <= paths


def test_every_provider_route_requires_a_signed_in_user() -> None:
    missing = sorted(
        f"{sorted(route.methods)} {path}"
        for path, route in _provider_routes()
        if path not in PUBLIC_PROVIDER_ROUTES and not _requires_user(route)
    )
    assert not missing, (
        "These Swiggy/Zepto routes reach a linked provider account without sign-in. "
        f"Add `_user: UserIdDependency` or list them in PUBLIC_PROVIDER_ROUTES: {missing}"
    )


class _MustNotBeCalled:
    def __getattr__(self, name: str):
        raise AssertionError(f"provider service touched without sign-in: {name}")


def test_unauthenticated_provider_reads_are_rejected_before_any_provider_call() -> None:
    settings = get_settings().model_copy(update={"clerk_secret_key": SecretStr("sk_test_dummy")})
    user_override = app.dependency_overrides.pop(require_user_id, None)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_swiggy_service] = _MustNotBeCalled
    app.dependency_overrides[get_zepto_service] = _MustNotBeCalled
    try:
        with TestClient(app) as client:
            responses = {
                "zepto cart": client.get("/api/zepto/cart"),
                "zepto orders": client.get("/api/zepto/orders"),
                "zepto search": client.post("/api/zepto/search", json={"query": "atta"}),
                "swiggy search": client.post("/api/swiggy/search", json={"query": "atta"}),
                "swiggy connection": client.get("/api/swiggy/connection"),
            }
    finally:
        for dependency in (get_settings, get_swiggy_service, get_zepto_service):
            app.dependency_overrides.pop(dependency, None)
        if user_override is not None:
            app.dependency_overrides[require_user_id] = user_override
    assert {name: response.status_code for name, response in responses.items()} == {
        name: 401 for name in responses
    }
