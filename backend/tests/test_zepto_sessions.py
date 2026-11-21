import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.commerce.zepto.client import _SessionState, ZeptoMcpClient
from app.commerce.zepto.errors import ZeptoProviderError, ZeptoRateLimited
from app.commerce.zepto.models import ToolDescription
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
                    name="search",
                    description="Search products",
                    inputSchema={"type": "object"},
                )
            ]
        )


class PersistentSessionClient(ZeptoMcpClient):
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


async def test_reuses_one_zepto_session_and_catalogue() -> None:
    client = PersistentSessionClient(["same-token", "same-token"])

    first, second = await asyncio.gather(client.list_tools(), client.list_tools())

    assert [tool.name for tool in first] == ["search"]
    assert [tool.name for tool in second] == ["search"]
    assert client.opened_tokens == ["same-token"]
    assert client.sessions[0].list_calls == 1

    await client.aclose()
    assert client.resources[0].closed is True


async def test_zepto_token_change_replaces_session_and_catalogue() -> None:
    client = PersistentSessionClient(["old-token", "fresh-token"])

    await client.list_tools()
    await client.list_tools()

    assert client.opened_tokens == ["old-token", "fresh-token"]
    assert client.resources[0].closed is True
    assert [session.list_calls for session in client.sessions] == [1, 1]


async def test_zepto_broken_read_reconnects_once_but_write_does_not() -> None:
    read_client = PersistentSessionClient(["same-token"])
    attempts = 0

    async def read(_: FakeSession) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("connection closed")
        return "restored"

    assert await read_client._with_session(read) == "restored"  # type: ignore[arg-type]
    assert read_client.opened_tokens == ["same-token", "same-token"]

    write_client = PersistentSessionClient(["same-token"])

    async def write(_: FakeSession) -> None:
        raise RuntimeError("connection closed")

    with pytest.raises(ZeptoProviderError, match="not replayed"):
        await write_client._with_session(write, retry_on_auth=False)  # type: ignore[arg-type]
    assert write_client.opened_tokens == ["same-token"]


async def test_zepto_rate_limit_starts_local_cooldown() -> None:
    client = PersistentSessionClient(["same-token", "same-token"])

    async def limited(_: FakeSession) -> None:
        raise RuntimeError("HTTP 429 Too Many Requests")

    with pytest.raises(ZeptoRateLimited):
        await client._with_session(limited)  # type: ignore[arg-type]

    with pytest.raises(ZeptoRateLimited):
        await client.list_tools()

    assert client.opened_tokens == ["same-token"]


async def test_zepto_tool_result_rate_limit_starts_local_cooldown() -> None:
    class LimitedSession(FakeSession):
        async def call_tool(self, name: str, arguments: dict[str, object]):
            del name, arguments
            return SimpleNamespace(
                isError=True,
                structuredContent={"error": {"message": "HTTP 429 Too Many Requests"}},
                content=[],
            )

    client = PersistentSessionClient(["same-token"])
    session = LimitedSession()
    client._session = _SessionState(
        "same-token",
        session,
        FakeResources(),
        catalogue={
            "search": ToolDescription(
                name="search",
                input_schema={"type": "object", "properties": {}},
            )
        },
    )  # type: ignore[arg-type]

    with pytest.raises(ZeptoRateLimited):
        await client.call_tool("search", {})
    with pytest.raises(ZeptoRateLimited):
        await client.list_tools()


async def test_successful_plain_text_tool_result_is_preserved() -> None:
    class TextSession(FakeSession):
        async def call_tool(self, name: str, arguments: dict[str, object]):
            del name, arguments
            return SimpleNamespace(
                isError=False,
                structuredContent=None,
                content=[SimpleNamespace(text="provider text")],
            )

    client = PersistentSessionClient(["same-token"])
    session = TextSession()
    resources = FakeResources()
    client._session = _SessionState("same-token", session, resources)  # type: ignore[arg-type]

    result = await client.call_tool("search", {})

    assert result == {"content_text": "provider text"}


async def test_zepto_session_context_is_closed_by_its_owner_task(monkeypatch) -> None:
    from app.commerce.zepto import client as client_module

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
    client = ZeptoMcpClient(
        Settings(_env_file=None), FakeOAuth(["unused"])
    )  # type: ignore[arg-type]

    state = await client._open_session("token")
    request_task = asyncio.current_task()
    await state.resources.aclose()

    assert transport_tasks[0] is transport_tasks[1]
    assert transport_tasks[0] is not request_task
