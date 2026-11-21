from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.commerce.zepto.errors import ZeptoProviderError
from app.commerce.zepto.models import CapabilitySummary
from app.commerce.zepto.oauth import ZeptoOAuthClient
from app.commerce.zepto.service import ZeptoService
from app.commerce.zepto.store import EncryptedZeptoConnectionStore
from app.core.config import Settings
from app.dependencies import get_zepto_service
from app.main import app


def zepto_settings(tmp_path: Path, key: str, *, scope: str = "tools:read") -> Settings:
    return Settings(
        _env_file=None,
        app_encryption_key=key,
        zepto_authorization_server_url="https://auth.zepto.test",
        zepto_resource_url="https://mcp.zepto.test",
        zepto_mcp_url="https://mcp.zepto.test/mcp",
        zepto_oauth_redirect_uri="http://localhost/callback",
        zepto_oauth_scope=scope,
        zepto_token_store_path=tmp_path / "zepto_connection.enc",
    )


@pytest.mark.asyncio
async def test_zepto_oauth_uses_pkce_resource_and_encrypted_storage(
    tmp_path: Path,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/register":
            return httpx.Response(201, json={"client_id": "zepto-dynamic-client"})
        if request.url.path == "/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "zepto-access-token",
                    "refresh_token": "zepto-refresh-token",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "scope": "tools:read",
                },
            )
        return httpx.Response(404)

    key = Fernet.generate_key().decode()
    settings = zepto_settings(tmp_path, key)
    store = EncryptedZeptoConnectionStore(settings.zepto_token_store_path, key)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        oauth = ZeptoOAuthClient(settings, store, http_client)
        authorization_url = await oauth.authorization_url()
        query = parse_qs(urlparse(authorization_url).query)
        assert query["code_challenge_method"] == ["S256"]
        assert query["scope"] == ["tools:read"]
        assert query["resource"] == ["https://mcp.zepto.test"]
        assert "code_challenge" in query
        restarted_oauth = ZeptoOAuthClient(settings, store, http_client)
        await restarted_oauth.exchange_code(
            "authorization-code", query["state"][0], "https://auth.zepto.test"
        )

    connection = await store.load()
    assert connection is not None
    assert connection.access_token == "zepto-access-token"
    encrypted = settings.zepto_token_store_path.read_bytes()
    assert b"zepto-access-token" not in encrypted
    assert settings.zepto_token_store_path.stat().st_mode & 0o777 == 0o600
    assert [request.url.path for request in seen] == ["/register", "/token"]


@pytest.mark.asyncio
async def test_zepto_oauth_rejects_wrong_issuer_before_token_exchange(
    tmp_path: Path,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"client_id": "zepto-dynamic-client"})

    key = Fernet.generate_key().decode()
    settings = zepto_settings(tmp_path, key)
    store = EncryptedZeptoConnectionStore(settings.zepto_token_store_path, key)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        oauth = ZeptoOAuthClient(settings, store, http_client)
        authorization_url = await oauth.authorization_url()
        state = parse_qs(urlparse(authorization_url).query)["state"][0]
        with pytest.raises(ZeptoProviderError, match="issuer"):
            await oauth.exchange_code("authorization-code", state, "https://evil.test")

    assert [request.url.path for request in seen] == ["/register"]


@pytest.mark.asyncio
async def test_zepto_refuses_broader_scope_before_registering_client(tmp_path: Path) -> None:
    key = Fernet.generate_key().decode()
    settings = zepto_settings(tmp_path, key, scope="tools:read orders:write")
    store = EncryptedZeptoConnectionStore(settings.zepto_token_store_path, key)
    oauth = ZeptoOAuthClient(settings, store)

    with pytest.raises(ZeptoProviderError, match="broader"):
        await oauth.authorization_url()
    assert not settings.zepto_token_store_path.exists()


@pytest.mark.asyncio
async def test_zepto_capability_summary_does_not_discover_tools_while_disconnected() -> None:
    class FakeOAuth:
        async def status(self):
            from app.commerce.zepto.models import ConnectionStatus

            return ConnectionStatus(connected=False)

    class NeverCalledClient:
        async def list_tools(self):  # pragma: no cover - a failure would be enough
            raise AssertionError("an unconnected account must not call the MCP server")

    service = ZeptoService(FakeOAuth(), NeverCalledClient())  # type: ignore[arg-type]
    assert await service.capabilities() == CapabilitySummary(
        connected=False, catalogue_discovered=False
    )


def test_zepto_capabilities_endpoint_is_explicitly_not_checkout() -> None:
    class FakeService:
        async def capabilities(self) -> CapabilitySummary:
            return CapabilitySummary(
                connected=True,
                catalogue_discovered=True,
                remote_tool_names=["some_provider_tool"],
            )

    app.dependency_overrides[get_zepto_service] = lambda: FakeService()
    try:
        with TestClient(app) as client:
            response = client.get("/api/zepto/capabilities")
    finally:
        app.dependency_overrides.pop(get_zepto_service, None)

    assert response.status_code == 200
    assert response.json() == {
        "connected": True,
        "catalogue_discovered": True,
        "remote_tool_names": ["some_provider_tool"],
        "exposed_actions": ["connect", "disconnect", "inspect_catalogue"],
        "cart_actions_exposed": False,
        "cart_read_exposed": False,
        "order_history_exposed": False,
        "checkout_exposed": False,
    }
