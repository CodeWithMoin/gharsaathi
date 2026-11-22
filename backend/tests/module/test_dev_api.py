"""``/api/dev/*``: hidden (404) unless enabled, loopback, not proxied, not private, signed in."""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.dev import get_dev_data, get_dev_doctor, get_dev_reconciler
from app.auth import require_user_id
from app.core.config import Settings, get_settings
from app.devtools.budget import ProviderCooldown
from app.devtools.doctor import Check
from app.devtools.trace import TraceWriter
from app.main import app
from tests.routes import DEV_ROUTES, concrete_path, operations

LOCAL = ("127.0.0.1", 51000)


class FakeData:
    def __init__(self) -> None:
        self.cleared: list[tuple[str, str]] = []

    async def person(self, user_id):
        return SimpleNamespace(external_id=user_id)

    async def memories(self, actor):
        return [{"id": "m1", "status": "confirmed", "text": "Prefers Amul"}]

    async def zepto_address(self, actor):
        return {"selected": True, "snapshot_age_min": 3}

    async def baskets(self, user_id):
        return [{"provider": "zepto", "item_total": "₹50", "locked": False, "lines": []}]

    def watches(self, user_id):
        return []

    async def notifications(self, user_id):
        return []

    async def clear_basket(self, user_id, provider):
        self.cleared.append((user_id, provider))
        return {"provider": provider, "cleared": True, "removed_lines": 1, "revision": 2}


@pytest.fixture
def dev(tmp_path):
    data = FakeData()
    settings = Settings(_env_file=None, dev_tools_enabled=True, dev_trace=True, dev_trace_dir=tmp_path / "traces")

    async def doctor():
        return [Check("Backend :8000", "green", "ok")]

    calls: list[str] = []

    def reconciler():
        async def run(out: io.StringIO):
            calls.append("reconcile")
            if settings.zepto_oauth_scope == "cooldown":
                raise ProviderCooldown("Zepto has a recorded rate-limit cooldown; no call was made.")
            print("mode=dry-run unknown_attempts=0", file=out)
            return {"attempts": 0}

        return run

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_dev_data] = lambda: data
    app.dependency_overrides[get_dev_doctor] = lambda: doctor
    app.dependency_overrides[get_dev_reconciler] = reconciler
    try:
        yield SimpleNamespace(settings=settings, data=data, calls=calls)
    finally:
        for dependency in (get_settings, get_dev_data, get_dev_doctor, get_dev_reconciler):
            app.dependency_overrides.pop(dependency, None)


def request_all(client: TestClient, **kwargs) -> dict[str, int]:
    results = {}
    for method, path, _ in operations(app):
        if not path.startswith("/api/dev/"):
            continue
        body = {"json": {"provider": "zepto"}} if method == "POST" else {}
        results[f"{method} {path}"] = client.request(method, concrete_path(path), **body, **kwargs).status_code
    return results


def test_every_dev_route_is_listed_and_requires_a_user() -> None:
    mounted = {f"{m} {p}" for m, p, _ in operations(app) if p.startswith("/api/dev/")}
    assert mounted == set(DEV_ROUTES)


def test_disabled_by_default_everything_404s(dev) -> None:
    dev.settings.dev_tools_enabled = False
    results = request_all(TestClient(app, client=LOCAL))
    assert set(results.values()) == {404}
    assert dev.data.cleared == [] and dev.calls == []


def test_non_loopback_and_proxied_requests_404(dev) -> None:
    assert set(request_all(TestClient(app, client=("10.0.1.7", 443))).values()) == {404}
    assert set(request_all(TestClient(app)).values()) == {404}  # "testclient" host
    assert set(request_all(TestClient(app, client=LOCAL), headers={"X-Forwarded-For": "203.0.113.9"}).values()) == {404}
    assert dev.data.cleared == []


def test_private_deployment_404s_even_for_the_owner(dev) -> None:
    dev.settings.allowed_user_ids = "test-user"
    assert set(request_all(TestClient(app, client=LOCAL)).values()) == {404}


def test_missing_identity_looks_like_a_missing_route(dev, monkeypatch) -> None:
    dev.settings.clerk_secret_key = SecretStr("sk_test_dummy")
    identity = app.dependency_overrides.pop(require_user_id)
    try:
        results = request_all(TestClient(app, client=LOCAL))
    finally:
        app.dependency_overrides[require_user_id] = identity
    assert set(results.values()) == {404}


def test_enabled_on_localhost_for_a_signed_in_user(dev) -> None:
    TraceWriter(dev.settings.dev_trace_dir).write({"turn_id": "t1", "rounds": []})
    TraceWriter(dev.settings.dev_trace_dir).write({"turn_id": "t2", "rounds": []})
    client = TestClient(app, client=LOCAL)
    assert client.get("/api/dev/status").json() == {"enabled": True, "trace": True, "trace_keep": 500}
    assert [t["turn_id"] for t in client.get("/api/dev/traces").json()["turns"]] == ["t2", "t1"]
    state = client.get("/api/dev/state").json()
    assert state["baskets"][0]["item_total"] == "₹50" and state["memory"][0]["text"] == "Prefers Amul"
    assert client.post("/api/dev/basket/clear", json={"provider": "zepto"}).json()["cleared"] is True
    assert dev.data.cleared == [("test-user", "zepto")]
    assert client.post("/api/dev/basket/clear", json={"provider": "amazon"}).status_code == 422
    assert client.post("/api/dev/doctor").json()["exit_code"] == 0
    assert client.post("/api/dev/reconcile-dry-run").json()["output"] == ["mode=dry-run unknown_attempts=0"]


def test_reconcile_under_a_cooldown_is_refused_without_a_call(dev) -> None:
    dev.settings.zepto_oauth_scope = "cooldown"
    response = TestClient(app, client=LOCAL).post("/api/dev/reconcile-dry-run")
    assert response.status_code == 409 and "no call was made" in response.json()["detail"]
