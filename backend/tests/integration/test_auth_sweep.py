"""Every authenticated route rejects a request with no Clerk session (real app + DB).

Clerk is configured with a dummy secret so `require_user_id` runs for real; no
token is sent, so the SDK never needs the network. Provider services raise if
touched, proving rejection happens before any provider work.
"""

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.auth import require_user_id
from app.core.config import get_settings
from app.dependencies import (
    get_swiggy_service,
    get_voice_service,
    get_zepto_checkout_service,
    get_zepto_service,
)
from app.main import app
from tests.routes import PUBLIC_ROUTES, concrete_path, operations


class MustNotBeCalled:
    def __getattr__(self, name: str):
        raise AssertionError(f"provider-backed service touched without sign-in: {name}")


def test_every_authenticated_route_rejects_a_missing_identity(api: TestClient, override) -> None:
    settings = get_settings().model_copy(update={"clerk_secret_key": SecretStr("sk_test_dummy")})
    override(get_settings, lambda: settings)
    for dependency in (get_swiggy_service, get_zepto_service, get_zepto_checkout_service, get_voice_service):
        override(dependency, MustNotBeCalled)
    test_identity = app.dependency_overrides.pop(require_user_id)
    try:
        results = {}
        for method, path, _ in operations(app):
            key = f"{method} {path}"
            if key in PUBLIC_ROUTES:
                continue
            kwargs = {"json": {}} if method in {"POST", "PUT", "PATCH"} else {}
            results[key] = api.request(method, concrete_path(path), **kwargs).status_code
    finally:
        app.dependency_overrides[require_user_id] = test_identity

    assert len(results) >= 55
    not_rejected = {key: code for key, code in results.items() if code != 401}
    assert not not_rejected, not_rejected


def test_public_routes_answer_without_identity(api: TestClient) -> None:
    assert api.get("/health").json() == {"status": "ok"}
    assert api.get("/api/support/agent/tools").status_code == 200
    # Unknown voice sessions and foreign origins are refused even though the route is public.
    events = "/api/voice/sessions/unknown-session/events"
    assert api.get(events, headers={"Origin": "https://evil.example"}).status_code == 403
    allowed_origin = get_settings().allowed_origins[0]
    assert api.get(events, headers={"Origin": allowed_origin}).status_code == 404
