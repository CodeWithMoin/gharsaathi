"""Rolling summaries, chat context loading and voice injection, with SQLite and fakes."""

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.conversations.context import ConversationContextLoader, RecentConversationContext, ContextTurn
from app.conversations.summarizer import BackgroundConversationSummaries, SummaryOutput
from app.core.config import Settings
from app.database.base import Base
from app.database.models import Conversation
from app.database.session import get_db_session
from app.dependencies import get_conversation_summaries
from app.main import app
from app.voice.service import LiveSessionService


class FakeSummarizer:
    model = "fake-summary-model"

    def __init__(self) -> None:
        self.calls: list[tuple[list[Mapping[str, str]], str]] = []
        self.release: asyncio.Event | None = None
        self.fail = False

    async def summarize(self, transcript: Sequence[Mapping[str, str]], previous: str) -> SummaryOutput | None:
        self.calls.append((list(transcript), previous))
        if self.release is not None:
            await self.release.wait()
        if self.fail:
            raise RuntimeError("model down")
        return SummaryOutput(
            wanted=f"atta aur dahi ({len(transcript)} turns)",
            preferences=["dikha do, batana mat"],
            open_items=[],
        )


@pytest_asyncio.fixture
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'summaries.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def summarizer() -> FakeSummarizer:
    return FakeSummarizer()


@pytest_asyncio.fixture
async def summaries(factory: async_sessionmaker[AsyncSession], summarizer: FakeSummarizer) -> BackgroundConversationSummaries:
    return BackgroundConversationSummaries(factory, summarizer, timeout_seconds=2)


@pytest_asyncio.fixture
async def client(
    factory: async_sessionmaker[AsyncSession], summaries: BackgroundConversationSummaries
) -> AsyncIterator[AsyncClient]:
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_conversation_summaries] = lambda: summaries
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    await summaries.wait_idle()
    app.dependency_overrides.pop(get_db_session, None)


def user(user_id: str) -> dict[str, str]:
    return {"X-Test-User-Id": user_id}


def chat(turns: int, first: str = "Atta chahiye", **extra: object) -> dict[str, object]:
    return {
        "messages": [
            {
                "id": f"m{i}",
                "role": "user" if i % 2 == 0 else "assistant",
                "content": first if i == 0 else f"turn {i}",
                "modality": "voice",
                "created_at": "2026-11-22T10:00:00Z",
            }
            for i in range(turns)
        ],
        **extra,
    }


async def rows(factory: async_sessionmaker[AsyncSession]) -> list[Conversation]:
    async with factory() as session:
        return list((await session.execute(select(Conversation).order_by(Conversation.created_at))).scalars())


@pytest.mark.asyncio
async def test_save_summarises_only_after_six_new_turns(client, summaries, summarizer, factory) -> None:
    await client.put("/api/conversations/current", headers=user("moin"), json=chat(5))
    await summaries.wait_idle()
    assert summarizer.calls == []

    saved = await client.put("/api/conversations/current", headers=user("moin"), json=chat(6))
    await summaries.wait_idle()
    assert len(summarizer.calls) == 1
    [row] = await rows(factory)
    assert row.summary is not None
    assert row.summary["turn_count"] == 6 and row.summary["model"] == "fake-summary-model"
    assert row.summary["text"].startswith("Wanted: atta aur dahi (6 turns)")
    # Writing the summary does not reorder the recent-chats list.
    assert row.updated_at.replace(tzinfo=UTC) == datetime.fromisoformat(saved.json()["updated_at"]).replace(tzinfo=UTC)
    # The summary is server-side only.
    assert "summary" not in (await client.get("/api/conversations/current", headers=user("moin"))).json()

    await client.put("/api/conversations/current", headers=user("moin"), json=chat(11))
    await summaries.wait_idle()
    assert len(summarizer.calls) == 1
    await client.put("/api/conversations/current", headers=user("moin"), json=chat(12))
    await summaries.wait_idle()
    assert len(summarizer.calls) == 2
    # The earlier summary is passed on as data for the rolling update.
    assert summarizer.calls[1][1].startswith("Wanted: atta aur dahi (6 turns)")


@pytest.mark.asyncio
async def test_save_returns_without_waiting_for_the_summariser(client, summaries, summarizer) -> None:
    summarizer.release = asyncio.Event()
    response = await asyncio.wait_for(
        client.put("/api/conversations/current", headers=user("moin"), json=chat(6)), 2
    )
    assert response.status_code == 200
    await asyncio.sleep(0.05)
    assert len(summarizer.calls) == 1  # started in the background, still waiting
    summarizer.release.set()
    await summaries.wait_idle()


@pytest.mark.asyncio
async def test_new_chat_summarises_the_archived_chat(client, summaries, summarizer, factory) -> None:
    await client.put("/api/conversations/current", headers=user("moin"), json=chat(2))
    await summaries.wait_idle()
    assert summarizer.calls == []
    assert (await client.post("/api/conversations/new", headers=user("moin"))).status_code == 204
    await summaries.wait_idle()
    [row] = await rows(factory)
    assert row.archived_at is not None and row.summary["turn_count"] == 2


@pytest.mark.asyncio
async def test_opening_another_chat_summarises_the_one_it_archives(client, summaries, summarizer, factory) -> None:
    first = await client.put("/api/conversations/current", headers=user("moin"), json=chat(2))
    await client.post("/api/conversations/new", headers=user("moin"))
    await summaries.wait_idle()
    await client.put("/api/conversations/current", headers=user("moin"), json=chat(3, "Dahi chahiye"))
    await client.post(f"/api/conversations/{first.json()['id']}/open", headers=user("moin"))
    await summaries.wait_idle()
    assert [row.summary["turn_count"] for row in await rows(factory)] == [2, 3]


@pytest.mark.asyncio
async def test_chats_without_messages_are_never_summarised(client, summaries, summarizer, factory) -> None:
    await client.put(
        "/api/conversations/current", headers=user("moin"), json={"submitted_query": "atta", "searched": True}
    )
    await client.post("/api/conversations/new", headers=user("moin"))
    await summaries.wait_idle()
    assert summarizer.calls == []
    assert [row.summary for row in await rows(factory)] == [None]


@pytest.mark.asyncio
async def test_summariser_failure_is_silent_and_leaves_no_summary(client, summaries, summarizer, factory) -> None:
    summarizer.fail = True
    response = await client.put("/api/conversations/current", headers=user("moin"), json=chat(6))
    await summaries.wait_idle()
    assert response.status_code == 200
    assert [row.summary for row in await rows(factory)] == [None]


@pytest.mark.asyncio
async def test_summary_job_for_a_deleted_chat_writes_nothing(client, summaries, summarizer, factory) -> None:
    summarizer.release = asyncio.Event()
    await client.put("/api/conversations/current", headers=user("moin"), json=chat(6))
    async with factory() as session:
        await session.execute(delete(Conversation))
        await session.commit()
    summarizer.release.set()
    await summaries.wait_idle()
    assert await rows(factory) == []


@pytest.mark.asyncio
async def test_list_can_exclude_empty_chats(client) -> None:
    await client.put("/api/conversations/current", headers=user("moin"), json={"submitted_query": ""})
    await client.post("/api/conversations/new", headers=user("moin"))
    await client.put("/api/conversations/current", headers=user("moin"), json=chat(2, "Dahi aur atta"))
    everything = (await client.get("/api/conversations", headers=user("moin"))).json()
    filtered = (await client.get("/api/conversations?exclude_empty=true", headers=user("moin"))).json()
    assert len(everything) == 2
    assert [item["title"] for item in filtered] == ["Dahi aur atta"]


@pytest.mark.asyncio
async def test_loader_uses_summaries_falls_back_and_stays_user_scoped(client, summaries, factory) -> None:
    # Moin: an archived, summarised chat, an archived chat without a summary, and a current chat.
    await client.put("/api/conversations/current", headers=user("moin"), json=chat(2, "Amul dahi chahiye"))
    await client.post("/api/conversations/new", headers=user("moin"))
    await summaries.wait_idle()
    app.dependency_overrides.pop(get_conversation_summaries)  # the next archive is not summarised
    await client.put("/api/conversations/current", headers=user("moin"), json=chat(2, "Shampoo chahiye"))
    await client.post("/api/conversations/new", headers=user("moin"))
    await client.put("/api/conversations/current", headers=user("moin"), json=chat(3, "Aashirvaad atta"))
    # Someone else's chat must never appear.
    await client.put("/api/conversations/current", headers=user("other"), json=chat(2, "Secret other chat"))

    loader = ConversationContextLoader(factory)
    context = await loader.load("moin")
    assert context is not None
    assert [turn.text for turn in context.turns][0] == "Aashirvaad atta"
    assert context.summary == ""  # the current chat has no summary yet: turns only
    joined = "\n".join(context.previous_chats)
    assert "Wanted: atta aur dahi (2 turns)" in joined  # stored summary
    assert 'asked "Shampoo chahiye"' in joined  # deterministic fallback
    assert "Secret other chat" not in joined + str(context.turns)

    other = await loader.load("other")
    assert other is not None and other.previous_chats == ()
    assert await loader.load("nobody") is None


@pytest.mark.asyncio
async def test_loader_ignores_chats_older_than_thirty_days(client, summaries, factory) -> None:
    await client.put("/api/conversations/current", headers=user("moin"), json=chat(2, "Old atta chat"))
    await client.post("/api/conversations/new", headers=user("moin"))
    await summaries.wait_idle()
    async with factory() as session:
        await session.execute(update(Conversation).values(updated_at=datetime.now(UTC) - timedelta(days=31)))
        await session.commit()
    assert await ConversationContextLoader(factory).load("moin") is None


class FixedContext:
    def __init__(self, context: RecentConversationContext | None, fail: bool = False) -> None:
        self.context = context
        self.fail = fail

    async def load(self, external_user_id: str) -> RecentConversationContext | None:
        assert external_user_id == "moin"
        if self.fail:
            raise RuntimeError("db down")
        return self.context


class Hub:
    def start(self, *args: object, **kwargs: object) -> None:
        return None


async def create_session(reader: FixedContext | None) -> dict[str, object]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"session": {"id": "live_1"}, "transport": {"sdp": "answer"}})

    settings = Settings(_env_file=None, openai_api_key=SecretStr("sk-test"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await LiveSessionService(settings, None, Hub(), http, context_reader=reader).create(  # type: ignore[arg-type]
            "v=0\r\nm=audio 9\r\n", "addr", household_id="moin"
        )
    return json.loads(seen[0].content)["session"]


@pytest.mark.asyncio
async def test_voice_session_is_seeded_with_chat_context() -> None:
    context = RecentConversationContext(
        summary="Wanted: atta aur dahi.",
        turns=(ContextTurn("user", "Amul dahi dhoondhna aur Aashirvaad atta"), ContextTurn("assistant", "Haan")),
        on_screen=('Results "dahi": Desi Farms Malai Dahi (₹45, Zepto)',),
        previous_chats=('20 Nov; asked "doodh"',),
    )
    session = await create_session(FixedContext(context))
    assert session["input"] == [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Amul dahi dhoondhna aur Aashirvaad atta"}]},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Haan"}]},
    ]
    live = str(session["instructions"])
    backend = str(session["delegation"]["responses"]["instructions"])  # type: ignore[index]
    for text in ("Wanted: atta aur dahi.", "Desi Farms Malai Dahi", '20 Nov; asked "doodh"'):
        assert text in live and text in backend
    # Turns go to GPT-Live as history, and to the backend model in its instructions.
    assert "Amul dahi dhoondhna" not in live and "Amul dahi dhoondhna" in backend


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", [None, FixedContext(None), FixedContext(None, fail=True)])
async def test_voice_session_without_context_has_no_history(reader: FixedContext | None) -> None:
    session = await create_session(reader)
    assert "input" not in session
    assert "This chat so far" not in str(session["instructions"])


def result_group(key: str, label: str, seq: int) -> dict[str, object]:
    return {
        "item_key": key,
        "item_label": label,
        "query": f"{key} chahiye",
        "after_message_id": "m0",
        "seq": seq,
        "products": [{"product_id": key, "sku_id": key, "name": f"{label} 1 kg", "price": {"mrp": 50, "offer_price": 41}, "available": True}],
    }


@pytest.mark.asyncio
async def test_result_groups_persist_and_old_saved_state_still_loads(client, factory) -> None:
    body = chat(2, result_groups=[result_group("atta", "Atta", 1), result_group("dahi", "Dahi", 2)])
    await client.put("/api/conversations/current", headers=user("moin"), json=body)
    current = (await client.get("/api/conversations/current", headers=user("moin"))).json()
    assert [(group["item_key"], group["item_label"], group["seq"]) for group in current["result_groups"]] == [
        ("atta", "Atta", 1),
        ("dahi", "Dahi", 2),
    ]

    # A chat saved before result groups existed.
    async with factory() as session:
        await session.execute(update(Conversation).values(state={"submitted_query": "oil", "searched": True, "products": []}))
        await session.commit()
    old = (await client.get("/api/conversations/current", headers=user("moin"))).json()
    assert old["result_groups"] == [] and old["submitted_query"] == "oil"


@pytest.mark.asyncio
async def test_chat_list_reports_message_count_and_items(client) -> None:
    groups = [result_group(key, key.title(), seq) for seq, key in enumerate(["atta", "dahi", "atta2", "oil", "ghee", "chai"])]
    await client.put("/api/conversations/current", headers=user("moin"), json=chat(3, "Atta aur dahi", result_groups=groups))
    [listed] = (await client.get("/api/conversations?exclude_empty=true", headers=user("moin"))).json()
    assert listed["title"] == "Atta aur dahi"
    assert listed["message_count"] == 3
    assert listed["items"] == ["Chai", "Ghee", "Oil", "Atta2", "Dahi"]  # newest first, at most five


@pytest.mark.asyncio
async def test_loader_on_screen_state_lists_each_result_group(client, factory) -> None:
    body = chat(2, result_groups=[result_group("atta", "Atta", 1), result_group("dahi", "Dahi", 2)])
    await client.put("/api/conversations/current", headers=user("moin"), json=body)
    context = await ConversationContextLoader(factory).load("moin")
    assert context is not None
    assert context.on_screen == (
        'Results "Dahi": Dahi 1 kg (₹41)',
        'Results "Atta": Atta 1 kg (₹41)',
    )
