from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet

from app.commerce.swiggy.errors import SwiggyProviderError
from app.commerce.swiggy.oauth import SwiggyOAuthClient
from app.commerce.swiggy.store import EncryptedConnectionStore
from app.core.config import Settings


def settings(tmp_path: Path, key: str) -> Settings:
    return Settings(
        _env_file=None,
        app_encryption_key=key,
        swiggy_token_store_path=tmp_path / "connection.enc",
        swiggy_oauth_redirect_uri="http://localhost:8000/api/swiggy/oauth/callback",
    )


@pytest.mark.asyncio
async def test_oauth_pkce_state_and_encrypted_persistence(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/auth/register":
            return httpx.Response(201, json={"client_id": "dynamic-client"})
        if request.url.path == "/auth/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "secret-access-token",
                    "refresh_token": "secret-refresh-token",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "scope": "mcp:tools",
                },
            )
        return httpx.Response(404)

    key = Fernet.generate_key().decode()
    app_settings = settings(tmp_path, key)
    store = EncryptedConnectionStore(app_settings.swiggy_token_store_path, key)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://mcp.swiggy.com"
    ) as client:
        oauth = SwiggyOAuthClient(app_settings, store, client)
        authorization_url = await oauth.authorization_url()
        assert await oauth.authorization_url() == authorization_url
        query = parse_qs(urlparse(authorization_url).query)
        assert query["code_challenge_method"] == ["S256"]
        assert query["scope"] == ["mcp:tools"]
        assert "code_challenge" in query
        restarted_oauth = SwiggyOAuthClient(app_settings, store, client)
        await restarted_oauth.exchange_code("authorization-code", query["state"][0])

    connection = await store.load()
    assert connection is not None
    assert connection.access_token == "secret-access-token"
    encrypted = app_settings.swiggy_token_store_path.read_bytes()
    assert b"secret-access-token" not in encrypted
    assert app_settings.swiggy_token_store_path.stat().st_mode & 0o777 == 0o600
    assert [request.url.path for request in seen] == ["/auth/register", "/auth/token"]

    await oauth.record_rate_limit(60)
    restarted_oauth = SwiggyOAuthClient(app_settings, store)
    assert await restarted_oauth.rate_limit_remaining() > 0

    await store.clear_tokens()
    disconnected = await store.load()
    assert disconnected is not None
    assert disconnected.client_id == "dynamic-client"
    assert disconnected.access_token is None
    assert disconnected.refresh_token is None
    assert disconnected.pending_authorizations == {}
    assert disconnected.rate_limited_until is None


@pytest.mark.asyncio
async def test_oauth_rejects_unknown_state_before_token_exchange(tmp_path: Path) -> None:
    token_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.path == "/auth/register":
            return httpx.Response(201, json={"client_id": "dynamic-client"})
        token_calls += 1
        return httpx.Response(500)

    key = Fernet.generate_key().decode()
    app_settings = settings(tmp_path, key)
    store = EncryptedConnectionStore(app_settings.swiggy_token_store_path, key)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        oauth = SwiggyOAuthClient(app_settings, store, client)
        await oauth.authorization_url()
        with pytest.raises(SwiggyProviderError, match="state"):
            await oauth.exchange_code("code", "wrong-state")
    assert token_calls == 0
