import httpx
import pytest

from app.oauth_readiness import local_callback_is_ready


class FakeAsyncClient:
    response = httpx.Response(200, json={"status": "ok"})
    requested_url = ""

    def __init__(self, **_: object) -> None:
        pass

    async def __aenter__(self) -> "FakeAsyncClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def get(self, url: str) -> httpx.Response:
        type(self).requested_url = url
        return type(self).response


@pytest.mark.asyncio
async def test_local_callback_probe_uses_loopback_health_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.oauth_readiness.httpx.AsyncClient", FakeAsyncClient)

    assert await local_callback_is_ready(
        "http://localhost/callback",
        expected_service="oauth-callback-bridge",
    ) is False
    assert FakeAsyncClient.requested_url == "http://127.0.0.1:80/health"

    FakeAsyncClient.response = httpx.Response(
        200,
        json={"status": "ok", "service": "oauth-callback-bridge"},
    )
    assert await local_callback_is_ready(
        "http://localhost/callback",
        expected_service="oauth-callback-bridge",
    ) is True


@pytest.mark.asyncio
async def test_remote_callback_does_not_require_local_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_called(**_: object) -> None:
        raise AssertionError("remote callback must not be probed as a local service")

    monkeypatch.setattr("app.oauth_readiness.httpx.AsyncClient", fail_if_called)
    assert await local_callback_is_ready("https://api.gharsaathi.app/oauth/callback") is True
