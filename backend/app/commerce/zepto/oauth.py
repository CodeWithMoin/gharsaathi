import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx

from app.commerce.zepto.errors import (
    ZeptoAuthenticationRequired,
    ZeptoProviderError,
)
from app.commerce.zepto.models import (
    ConnectionStatus,
    OAuthTokenResponse,
    PendingAuthorization,
    StoredConnection,
)
from app.commerce.zepto.store import EncryptedZeptoConnectionStore
from app.core.config import Settings


# The current product boundary is inspection-only.  Keeping this in the OAuth
# layer means a configuration typo cannot silently request broader account
# capabilities while the rest of the app has no safe use for them.
READ_ONLY_SCOPE = "tools:read"


class ZeptoOAuthClient:
    def __init__(
        self,
        settings: Settings,
        store: EncryptedZeptoConnectionStore,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self._http_client = http_client

    async def _request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        if self._http_client is not None:
            return await self._http_client.request(method, url, **kwargs)
        async with httpx.AsyncClient(timeout=20.0) as client:
            return await client.request(method, url, **kwargs)

    async def _register_client(self) -> str:
        self._ensure_read_only_scope()
        existing = await self.store.load()
        if existing is not None and existing.client_id:
            return existing.client_id

        response = await self._request(
            "POST",
            f"{self.settings.zepto_authorization_server_url}/register",
            json={
                "client_name": "GharSaathi local proof of concept",
                "redirect_uris": [self.settings.zepto_oauth_redirect_uri],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
                "scope": self.settings.zepto_oauth_scope,
            },
            headers={"Accept": "application/json"},
        )
        if response.status_code >= 400:
            raise ZeptoProviderError(
                f"Zepto client registration failed with HTTP {response.status_code}."
            )
        client_id = response.json().get("client_id")
        if not isinstance(client_id, str) or not client_id:
            raise ZeptoProviderError("Zepto client registration returned no client_id.")
        await self.store.save(StoredConnection(client_id=client_id))
        return client_id

    async def authorization_url(self) -> str:
        self._ensure_read_only_scope()
        client_id = await self._register_client()
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        now = datetime.now(UTC)
        await self.store.save_pending(
            state,
            PendingAuthorization(
                code_verifier=verifier,
                client_id=client_id,
                created_at=now,
            ),
        )
        query = urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": self.settings.zepto_oauth_redirect_uri,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
                "scope": self.settings.zepto_oauth_scope,
                "resource": self.settings.zepto_resource_url,
            }
        )
        return f"{self.settings.zepto_authorization_server_url}/authorize?{query}"

    async def exchange_code(self, code: str, state: str, issuer: str | None) -> None:
        expected_issuer = self.settings.zepto_authorization_server_url.rstrip("/")
        if issuer is not None and issuer.rstrip("/") != expected_issuer:
            raise ZeptoProviderError("Zepto OAuth issuer did not match the expected server.")
        pending = await self.store.pop_pending(state)
        if pending is None or datetime.now(UTC) - pending.created_at >= timedelta(minutes=10):
            raise ZeptoProviderError(
                "OAuth state is invalid or expired. Start the connection again."
            )
        response = await self._request(
            "POST",
            f"{self.settings.zepto_authorization_server_url}/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": pending.code_verifier,
                "client_id": pending.client_id,
                "redirect_uri": self.settings.zepto_oauth_redirect_uri,
                "resource": self.settings.zepto_resource_url,
            },
            headers={"Accept": "application/json"},
        )
        await self._save_token_response(response, pending.client_id)

    async def reject_authorization(self, state: str) -> None:
        pending = await self.store.pop_pending(state)
        if pending is None or datetime.now(UTC) - pending.created_at >= timedelta(minutes=10):
            raise ZeptoProviderError(
                "OAuth state is invalid or expired. Start the connection again."
            )

    async def _save_token_response(
        self,
        response: httpx.Response,
        client_id: str,
        fallback_refresh_token: str | None = None,
    ) -> str:
        if response.status_code >= 400:
            raise ZeptoProviderError(
                f"Zepto token exchange failed with HTTP {response.status_code}."
            )
        try:
            token = OAuthTokenResponse.model_validate(response.json())
        except (ValueError, TypeError) as exc:
            raise ZeptoProviderError("Zepto returned an invalid token response.") from exc
        self._ensure_read_only_scope(token.scope)
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

    def _ensure_read_only_scope(self, granted_scope: str | None = None) -> None:
        """Reject a broader OAuth grant until a reviewed capability is added.

        A token response is allowed to omit ``scope`` per OAuth conventions; in
        that case it represents the requested scope.  If Zepto sends an
        explicit scope, it must be exactly the read-only request.
        """

        requested = self.settings.zepto_oauth_scope.strip()
        if requested != READ_ONLY_SCOPE:
            raise ZeptoProviderError(
                "Zepto is configured for a scope broader than this read-only integration. "
                "Set ZEPTO_OAUTH_SCOPE=tools:read."
            )
        if granted_scope is not None and granted_scope.strip() != READ_ONLY_SCOPE:
            raise ZeptoProviderError(
                "Zepto returned a scope broader than this read-only integration. "
                "Reconnect after reviewing the provider grant."
            )

    async def access_token(self) -> str:
        connection = await self.store.load()
        if connection is None or not connection.access_token:
            raise ZeptoAuthenticationRequired("Connect your Zepto account first.")
        if connection.expires_at is None or connection.expires_at <= datetime.now(UTC) + timedelta(seconds=60):
            return await self.refresh(connection)
        return connection.access_token

    async def refresh(self, connection: StoredConnection | None = None) -> str:
        connection = connection or await self.store.load()
        if connection is None or not connection.refresh_token:
            raise ZeptoAuthenticationRequired("Reconnect your Zepto account.")
        response = await self._request(
            "POST",
            f"{self.settings.zepto_authorization_server_url}/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": connection.refresh_token,
                "client_id": connection.client_id,
                "resource": self.settings.zepto_resource_url,
            },
            headers={"Accept": "application/json"},
        )
        if response.status_code in {400, 401, 419}:
            await self.store.clear()
            raise ZeptoAuthenticationRequired(
                "Your Zepto session expired. Connect again."
            )
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
                    f"{self.settings.zepto_authorization_server_url}/revoke",
                    data={
                        "token": connection.access_token,
                        "client_id": connection.client_id,
                    },
                    headers={"Accept": "application/json"},
                )
            except httpx.HTTPError:
                pass
        await self.store.clear()
