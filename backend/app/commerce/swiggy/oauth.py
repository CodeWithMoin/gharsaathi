import asyncio
import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx

from app.commerce.swiggy.errors import (
    SwiggyAuthenticationRequired,
    SwiggyProviderError,
)
from app.commerce.swiggy.models import (
    ConnectionStatus,
    OAuthTokenResponse,
    StoredConnection,
)
from app.commerce.swiggy.store import EncryptedConnectionStore
from app.core.config import Settings


@dataclass(frozen=True)
class PendingAuthorization:
    code_verifier: str
    client_id: str
    created_at: datetime


class SwiggyOAuthClient:
    def __init__(
        self,
        settings: Settings,
        store: EncryptedConnectionStore,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self._http_client = http_client
        self._pending: dict[str, PendingAuthorization] = {}
        self._pending_lock = asyncio.Lock()

    async def _request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        if self._http_client is not None:
            return await self._http_client.request(method, url, **kwargs)
        async with httpx.AsyncClient(timeout=20.0) as client:
            return await client.request(method, url, **kwargs)

    async def _register_client(self) -> str:
        existing = await self.store.load()
        if existing is not None and existing.client_id:
            return existing.client_id

        response = await self._request(
            "POST",
            f"{self.settings.swiggy_base_url}/auth/register",
            json={
                "client_name": "GharCart local development",
                "redirect_uris": [self.settings.swiggy_oauth_redirect_uri],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
                "scope": "mcp:tools",
            },
            headers={"Accept": "application/json"},
        )
        if response.status_code >= 400:
            raise SwiggyProviderError(
                f"Swiggy client registration failed with HTTP {response.status_code}."
            )
        client_id = response.json().get("client_id")
        if not isinstance(client_id, str) or not client_id:
            raise SwiggyProviderError("Swiggy client registration returned no client_id.")
        await self.store.save(StoredConnection(client_id=client_id))
        return client_id

    async def authorization_url(self) -> str:
        client_id = await self._register_client()
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        now = datetime.now(UTC)
        async with self._pending_lock:
            self._pending = {
                key: pending
                for key, pending in self._pending.items()
                if now - pending.created_at < timedelta(minutes=10)
            }
            self._pending[state] = PendingAuthorization(verifier, client_id, now)
        query = urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": self.settings.swiggy_oauth_redirect_uri,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
                "scope": "mcp:tools",
            }
        )
        return f"{self.settings.swiggy_base_url}/auth/authorize?{query}"

    async def exchange_code(self, code: str, state: str) -> None:
        async with self._pending_lock:
            pending = self._pending.pop(state, None)
        if pending is None or datetime.now(UTC) - pending.created_at >= timedelta(minutes=10):
            raise SwiggyProviderError("OAuth state is invalid or expired. Start the connection again.")
        response = await self._request(
            "POST",
            f"{self.settings.swiggy_base_url}/auth/token",
            json={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": pending.code_verifier,
                "client_id": pending.client_id,
                "redirect_uri": self.settings.swiggy_oauth_redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
        await self._save_token_response(response, pending.client_id)

    async def reject_authorization(self, state: str) -> None:
        async with self._pending_lock:
            pending = self._pending.pop(state, None)
        if pending is None or datetime.now(UTC) - pending.created_at >= timedelta(minutes=10):
            raise SwiggyProviderError("OAuth state is invalid or expired. Start the connection again.")

    async def _save_token_response(
        self,
        response: httpx.Response,
        client_id: str,
        fallback_refresh_token: str | None = None,
    ) -> str:
        if response.status_code >= 400:
            raise SwiggyProviderError(
                f"Swiggy token exchange failed with HTTP {response.status_code}."
            )
        try:
            token = OAuthTokenResponse.model_validate(response.json())
        except (ValueError, TypeError) as exc:
            raise SwiggyProviderError("Swiggy returned an invalid token response.") from exc
        expires_at = datetime.now(UTC) + timedelta(seconds=token.expires_in)
        await self.store.save(
            StoredConnection(
                client_id=client_id,
                access_token=token.access_token,
                refresh_token=token.refresh_token or fallback_refresh_token,
                token_type=token.token_type,
                scope=token.scope,
                expires_at=expires_at,
            )
        )
        return token.access_token

    async def access_token(self) -> str:
        connection = await self.store.load()
        if connection is None or not connection.access_token:
            raise SwiggyAuthenticationRequired("Connect your Swiggy account first.")
        if connection.expires_at is None or connection.expires_at <= datetime.now(UTC) + timedelta(seconds=60):
            return await self.refresh(connection)
        return connection.access_token

    async def refresh(self, connection: StoredConnection | None = None) -> str:
        connection = connection or await self.store.load()
        if connection is None or not connection.refresh_token:
            raise SwiggyAuthenticationRequired("Reconnect your Swiggy account.")
        response = await self._request(
            "POST",
            f"{self.settings.swiggy_base_url}/auth/token",
            json={
                "grant_type": "refresh_token",
                "refresh_token": connection.refresh_token,
                "client_id": connection.client_id,
            },
            headers={"Accept": "application/json"},
        )
        if response.status_code in {400, 401, 419}:
            await self.store.clear()
            raise SwiggyAuthenticationRequired("Your Swiggy session expired. Connect again.")
        return await self._save_token_response(
            response, connection.client_id, connection.refresh_token
        )

    async def status(self) -> ConnectionStatus:
        connection = await self.store.load()
        return ConnectionStatus(
            connected=bool(connection and connection.access_token),
            expires_at=connection.expires_at if connection and connection.access_token else None,
        )

    async def disconnect(self) -> None:
        connection = await self.store.load()
        if connection and connection.access_token:
            try:
                await self._request(
                    "POST",
                    f"{self.settings.swiggy_base_url}/auth/logout",
                    headers={"Authorization": f"Bearer {connection.access_token}"},
                )
            except httpx.HTTPError:
                pass
        await self.store.clear()
