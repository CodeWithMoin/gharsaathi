import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.commerce.swiggy.client import _SessionState, SwiggyMcpClient
from app.commerce.swiggy.errors import SwiggyProviderError
from app.core.config import Settings


class FakeOAuth:
    def __init__(self, tokens: list[str]) -> None:
        self.tokens = iter(tokens)

    async def access_token(self) -> str:
        return next(self.tokens)


class FakeResources:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self) -> None:
        self.list_calls = 0

    async def list_tools(self) -> SimpleNamespace:
        self.list_calls += 1
        return SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name="search_products",
                    description="Search products",
                    inputSchema={"type": "object"},
                )
            ]
        )


class PersistentSessionClient(SwiggyMcpClient):
    def __init__(self, tokens: list[str]) -> None:
        super().__init__(Settings(_env_file=None), FakeOAuth(tokens))  # type: ignore[arg-type]
        self.opened_tokens: list[str] = []
        self.sessions: list[FakeSession] = []
        self.resources: list[FakeResources] = []

    async def _open_session(self, access_token: str) -> _SessionState:
        self.opened_tokens.append(access_token)
        session = FakeSession()
        resources = FakeResources()
        self.sessions.append(session)
        self.resources.append(resources)
        return _SessionState(access_token, session, resources)  # type: ignore[arg-type]


async def test_reuses_one_session_and_catalogue_for_repeated_reads() -> None:
    client = PersistentSessionClient(["same-token", "same-token"])

    first = await client.list_tools()
    second = await client.list_tools()

    assert [tool.name for tool in first] == ["search_products"]
    assert [tool.name for tool in second] == ["search_products"]
    assert client.opened_tokens == ["same-token"]
    assert client.sessions[0].list_calls == 1

    await client.aclose()
    await client.aclose()
    assert client.resources[0].closed is True


async def test_concurrent_reads_share_the_one_open_session() -> None:
    client = PersistentSessionClient(["same-token", "same-token"])

    await asyncio.gather(client.list_tools(), client.list_tools())

    assert client.opened_tokens == ["same-token"]
    assert client.sessions[0].list_calls == 1


async def test_token_change_replaces_session_and_invalidates_catalogue() -> None:
    client = PersistentSessionClient(["old-token", "fresh-token"])

    await client.list_tools()
    await client.list_tools()

    assert client.opened_tokens == ["old-token", "fresh-token"]
    assert client.resources[0].closed is True
    assert [session.list_calls for session in client.sessions] == [1, 1]


async def test_broken_read_session_is_reconnected_once() -> None:
    client = PersistentSessionClient(["same-token"])
    attempted_sessions: list[FakeSession] = []

    async def read(session: FakeSession) -> str:
        attempted_sessions.append(session)
        if len(attempted_sessions) == 1:
            raise RuntimeError("connection closed")
        return "recovered"

    assert await client._with_session(read) == "recovered"  # type: ignore[arg-type]
    assert client.opened_tokens == ["same-token", "same-token"]
    assert client.resources[0].closed is True


async def test_broken_consequential_request_is_not_replayed() -> None:
    client = PersistentSessionClient(["same-token"])

    async def consequential(_: FakeSession) -> None:
        raise RuntimeError("connection closed")

    with pytest.raises(SwiggyProviderError, match="not replayed"):
        await client._with_session(consequential, retry_on_auth=False)  # type: ignore[arg-type]

    assert client.opened_tokens == ["same-token"]


async def test_real_session_context_is_closed_by_its_owner_task(monkeypatch) -> None:
    from app.commerce.swiggy import client as client_module

    transport_tasks: list[asyncio.Task[object] | None] = []

    @asynccontextmanager
    async def transport(*args, **kwargs):
        del args, kwargs
        transport_tasks.append(asyncio.current_task())
        try:
            yield object(), object(), None
        finally:
            transport_tasks.append(asyncio.current_task())

    class Session:
        def __init__(self, *args):
            del args

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            del args

        async def initialize(self):
            return None

    monkeypatch.setattr(client_module, "streamablehttp_client", transport)
    monkeypatch.setattr(client_module, "ClientSession", Session)
    client = SwiggyMcpClient(
        Settings(_env_file=None), FakeOAuth(["unused"])
    )  # type: ignore[arg-type]

    state = await client._open_session("token")
    request_task = asyncio.current_task()
    await state.resources.aclose()

    assert transport_tasks[0] is transport_tasks[1]
    assert transport_tasks[0] is not request_task
