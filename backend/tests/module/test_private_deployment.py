"""Private (owner-only) deployment: ALLOWED_USER_IDS gates every route."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api import swiggy as swiggy_api
from app.api import zepto as zepto_api
from app.auth import (
    PrivateDeploymentForbidden,
    require_owner_if_private,
    require_user_id,
)
from app.core.config import Settings, get_settings
from app.dependencies import (
    get_swiggy_service,
    get_voice_service,
    get_zepto_checkout_service,
    get_zepto_service,
)
from app.main import app
from tests.routes import DEV_ROUTES, concrete_path, operations

OWNER = "user_owner"
INTRUDER = "user_intruder"

# Reachable without a Clerk session even when private; each is bound to state
# that only the owner can create (OAuth state, a voice session's events token).
STATE_BOUND_ROUTES = {
    "GET /health",
    "GET /api/swiggy/oauth/callback",
    "GET /api/zepto/oauth/callback",
    "GET /api/voice/sessions/{session_id}/events",
    # Vobiz signature: the carrier's webhooks, checked by HMAC, not a Clerk session.
    "POST /api/rider/vobiz/answer",
    "POST /api/rider/vobiz/after-stream",
}


def request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


def signed_in_as(monkeypatch: pytest.MonkeyPatch, subject: str) -> None:
    monkeypatch.setattr(
        "app.auth.authenticate_request",
        lambda *_: SimpleNamespace(is_signed_in=True, payload={"sub": subject}),
    )


def private_settings(**update: object) -> Settings:
    return Settings(
        _env_file=None,
        clerk_secret_key="sk_test_fake",
        allowed_user_ids=f" {OWNER} , user_second ",
        **update,
    )


class MustNotBeCalled:
    def __getattr__(self, name: str):
        raise AssertionError(f"provider-backed service touched by a non-owner: {name}")


class RecordingOAuthService:
    def __init__(self) -> None:
        self.started = 0

    async def authorization_url(self) -> str:
        self.started += 1
        return "https://auth.example.test/authorize?state=s"


def test_allow_list_parsing() -> None:
    assert private_settings().allowed_user_id_set == {OWNER, "user_second"}
    assert private_settings().private_deployment
    assert not Settings(_env_file=None).private_deployment


def test_owner_passes_and_everyone_else_is_refused(monkeypatch) -> None:
    signed_in_as(monkeypatch, OWNER)
    assert require_user_id(request(), private_settings()) == OWNER
    signed_in_as(monkeypatch, INTRUDER)
    with pytest.raises(PrivateDeploymentForbidden):
        require_user_id(request(), private_settings())


def test_without_an_allow_list_any_verified_user_passes(monkeypatch) -> None:
    signed_in_as(monkeypatch, INTRUDER)
    settings = Settings(_env_file=None, clerk_secret_key="sk_test_fake")
    assert require_user_id(request(), settings) == INTRUDER
    assert require_owner_if_private(request(), settings) is None


def test_every_route_refuses_a_signed_in_non_owner(monkeypatch) -> None:
    settings = private_settings()
    signed_in_as(monkeypatch, INTRUDER)
    identity = app.dependency_overrides.pop(require_user_id)
    app.dependency_overrides[get_settings] = lambda: settings
    for dependency in (get_swiggy_service, get_zepto_service, get_zepto_checkout_service, get_voice_service):
        app.dependency_overrides[dependency] = MustNotBeCalled
    try:
        results = {}
        dev_results: dict[str, int] = {}
        with TestClient(app) as client:
            for method, path, _ in operations(app):
                key = f"{method} {path}"
                if key in STATE_BOUND_ROUTES:
                    continue
                if key in DEV_ROUTES:
                    # Blocked in production: 404, never 403 or any data.
                    response = client.request(method, concrete_path(path), json={} if method == "POST" else None)
                    dev_results[key] = response.status_code
                    continue
                kwargs = {"json": {}} if method in {"POST", "PUT", "PATCH"} else {}
                response = client.request(method, concrete_path(path), follow_redirects=False, **kwargs)
                body = response.json() if response.headers.get("content-type", "").startswith("application/json") else None
                results[key] = (response.status_code, (body or {}).get("code"))
    finally:
        app.dependency_overrides[require_user_id] = identity
        for dependency in (get_settings, get_swiggy_service, get_zepto_service, get_zepto_checkout_service, get_voice_service):
            app.dependency_overrides.pop(dependency, None)

    assert "POST /api/voice/session" in results
    assert "GET /api/zepto/oauth/start" in results
    assert len(results) >= 60
    not_refused = {key: value for key, value in results.items() if value != (403, "private_deployment")}
    assert not not_refused, not_refused
    assert dev_results == {key: 404 for key in DEV_ROUTES}


def test_dev_routes_are_blocked_in_production_even_for_the_owner_on_localhost(monkeypatch) -> None:
    """A private (hosted) deployment never serves /api/dev, even with DEV_TOOLS_ENABLED set."""

    settings = private_settings(dev_tools_enabled=True)
    signed_in_as(monkeypatch, OWNER)
    identity = app.dependency_overrides.pop(require_user_id)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        client = TestClient(app, client=("127.0.0.1", 50000))
        codes = {
            key: client.request(key.split()[0], concrete_path(key.split()[1]), json={} if key.startswith("POST") else None).status_code
            for key in DEV_ROUTES
        }
    finally:
        app.dependency_overrides[require_user_id] = identity
        app.dependency_overrides.pop(get_settings, None)
    assert codes == {key: 404 for key in DEV_ROUTES}
    assert Settings(_env_file=None).dev_tools_enabled is False


@pytest.mark.parametrize("store", ["swiggy", "zepto"])
def test_owner_starts_oauth_with_a_signed_in_request(store: str, monkeypatch) -> None:
    async def ready(*args: object, **kwargs: object) -> bool:
        return True

    module, dependency = (zepto_api, get_zepto_service) if store == "zepto" else (swiggy_api, get_swiggy_service)
    monkeypatch.setattr(module, "local_callback_is_ready", ready)
    settings = private_settings()
    service = RecordingOAuthService()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[dependency] = lambda: service
    try:
        client = TestClient(app)
        started = client.post(f"/api/{store}/oauth/authorization")
        navigation = client.get(f"/api/{store}/oauth/start", follow_redirects=False)
    finally:
        app.dependency_overrides.pop(get_settings, None)
        app.dependency_overrides.pop(dependency, None)

    assert started.status_code == 200
    assert started.json() == {"authorization_url": "https://auth.example.test/authorize?state=s"}
    # The unauthenticated navigation is refused when private; only the POST creates state.
    assert navigation.status_code == 403
    assert navigation.json()["code"] == "private_deployment"
    assert service.started == 1


@pytest.mark.parametrize("store", ["swiggy", "zepto"])
def test_signed_in_oauth_start_still_checks_the_callback(store: str, monkeypatch) -> None:
    async def not_ready(*args: object, **kwargs: object) -> bool:
        return False

    module, dependency = (zepto_api, get_zepto_service) if store == "zepto" else (swiggy_api, get_swiggy_service)
    monkeypatch.setattr(module, "local_callback_is_ready", not_ready)
    service = RecordingOAuthService()
    app.dependency_overrides[dependency] = lambda: service
    try:
        response = TestClient(app).post(f"/api/{store}/oauth/authorization")
    finally:
        app.dependency_overrides.pop(dependency, None)

    assert response.status_code == 503
    assert service.started == 0

