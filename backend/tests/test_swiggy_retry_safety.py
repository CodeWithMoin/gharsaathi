import pytest

from app.commerce.swiggy.client import SwiggyMcpClient
from app.commerce.swiggy.errors import SwiggyProviderError
from app.core.config import Settings


class OAuthWithRefreshCounter:
    async def access_token(self) -> str:
        return "expired-token"

    def __init__(self) -> None:
        self.refresh_calls = 0

    async def refresh(self) -> str:
        self.refresh_calls += 1
        return "fresh-token"


class SessionFailureClient(SwiggyMcpClient):
    def __init__(self, oauth: OAuthWithRefreshCounter) -> None:
        super().__init__(Settings(_env_file=None), oauth)  # type: ignore[arg-type]
        self.tokens: list[str] = []

    async def _run_session(self, access_token: str, operation):  # type: ignore[no-untyped-def,override]
        self.tokens.append(access_token)
        if access_token == "expired-token":
            raise RuntimeError("401 unauthorized")
        return await operation(None)  # type: ignore[arg-type]


async def test_consequential_request_never_replays_after_auth_failure() -> None:
    oauth = OAuthWithRefreshCounter()
    client = SessionFailureClient(oauth)

    with pytest.raises(SwiggyProviderError, match="not replayed"):
        await client._with_session(lambda _: _answer(), retry_on_auth=False)

    assert oauth.refresh_calls == 0
    assert client.tokens == ["expired-token"]


async def test_read_operation_can_still_refresh_and_retry_after_auth_failure() -> None:
    oauth = OAuthWithRefreshCounter()
    client = SessionFailureClient(oauth)

    result = await client._with_session(lambda _: _answer())

    assert result == {"ok": True}
    assert oauth.refresh_calls == 1
    assert client.tokens == ["expired-token", "fresh-token"]


async def _answer() -> dict[str, bool]:
    return {"ok": True}
