from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.base import Base
from app.database.session import get_db_session
from app.main import app


@pytest_asyncio.fixture
async def conversation_client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'conversations.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client
    app.dependency_overrides.pop(get_db_session, None)
    await engine.dispose()


def headers(user_id: str) -> dict[str, str]:
    return {"X-Test-User-Id": user_id}


def state(query: str, *, messages: list[dict[str, str]] | None = None) -> dict[str, object]:
    return {
        "submitted_query": query,
        "searched": True,
        "messages": messages or [],
        "results_after_message_id": messages[-1]["id"] if messages else None,
        "products": [
            {
                "provider": "swiggy_instamart",
                "product_id": "product-1",
                "sku_id": "sku-1",
                "spin_id": "spin-1",
                "name": "Fortune oil",
                "price": {"mrp": 180, "offer_price": 160},
                "available": True,
            }
        ],
    }


@pytest.mark.asyncio
async def test_conversation_is_durable_and_scoped_to_user(
    conversation_client: AsyncClient,
) -> None:
    saved = await conversation_client.put(
        "/api/conversations/current",
        headers=headers("user-a"),
        json=state("oil"),
    )
    assert saved.status_code == 200

    restored = await conversation_client.get(
        "/api/conversations/current", headers=headers("user-a")
    )
    assert restored.status_code == 200
    assert restored.json()["submitted_query"] == "oil"
    assert restored.json()["products"][0]["name"] == "Fortune oil"

    other_user = await conversation_client.get(
        "/api/conversations/current", headers=headers("user-b")
    )
    assert other_user.status_code == 200
    assert other_user.json() is None


@pytest.mark.asyncio
async def test_voice_turns_are_persisted_and_restored_in_order(
    conversation_client: AsyncClient,
) -> None:
    voice_messages = [
        {
            "id": "voice-user-1",
            "role": "user",
            "content": "Do kilo atta chahiye",
            "modality": "voice",
            "created_at": "2026-11-21T10:00:00Z",
        },
        {
            "id": "voice-assistant-1",
            "role": "assistant",
            "content": "Bilkul, main options dekh rahi hoon.",
            "modality": "voice",
            "created_at": "2026-11-21T10:00:01Z",
        },
    ]
    saved = await conversation_client.put(
        "/api/conversations/current",
        headers=headers("voice-user"),
        json=state("atta", messages=voice_messages),
    )
    assert saved.status_code == 200
    assert saved.json()["messages"] == voice_messages

    restored = await conversation_client.get(
        "/api/conversations/current", headers=headers("voice-user")
    )
    assert restored.status_code == 200
    assert restored.json()["messages"] == voice_messages
    assert restored.json()["results_after_message_id"] == "voice-assistant-1"


@pytest.mark.asyncio
async def test_assistant_only_voice_greeting_can_start_a_conversation(
    conversation_client: AsyncClient,
) -> None:
    greeting = {
        "id": "voice-assistant-greeting",
        "role": "assistant",
        "content": "Namaste, aaj kya mangwana hai?",
        "modality": "voice",
        "created_at": "2026-11-21T10:00:00Z",
    }
    response = await conversation_client.put(
        "/api/conversations/current",
        headers=headers("voice-greeting-user"),
        json={
            "submitted_query": "",
            "products": [],
            "searched": False,
            "messages": [greeting],
        },
    )
    assert response.status_code == 200
    assert response.json()["submitted_query"] == ""
    assert response.json()["messages"] == [greeting]


@pytest.mark.asyncio
async def test_blank_chat_message_is_rejected(
    conversation_client: AsyncClient,
) -> None:
    response = await conversation_client.put(
        "/api/conversations/current",
        headers=headers("blank-message-user"),
        json={
            "submitted_query": "",
            "products": [],
            "searched": False,
            "messages": [
                {
                    "id": "voice-user-blank",
                    "role": "user",
                    "content": "   ",
                    "modality": "voice",
                    "created_at": "2026-11-21T10:00:00Z",
                }
            ],
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_new_chat_archives_previous_conversation(
    conversation_client: AsyncClient,
) -> None:
    await conversation_client.put(
        "/api/conversations/current",
        headers=headers("user-a"),
        json=state("atta"),
    )
    started = await conversation_client.post(
        "/api/conversations/new", headers=headers("user-a")
    )
    assert started.status_code == 204

    current = await conversation_client.get(
        "/api/conversations/current", headers=headers("user-a")
    )
    assert current.json() is None

    replacement = await conversation_client.put(
        "/api/conversations/current",
        headers=headers("user-a"),
        json=state("tea"),
    )
    assert replacement.json()["submitted_query"] == "tea"


@pytest.mark.asyncio
async def test_recent_chats_can_be_listed_searched_and_reopened(
    conversation_client: AsyncClient,
) -> None:
    first = await conversation_client.put(
        "/api/conversations/current",
        headers=headers("history-user"),
        json=state("monthly household list"),
    )
    await conversation_client.post(
        "/api/conversations/new", headers=headers("history-user")
    )
    second = await conversation_client.put(
        "/api/conversations/current",
        headers=headers("history-user"),
        json=state("pet supplies"),
    )

    recent = await conversation_client.get(
        "/api/conversations", headers=headers("history-user")
    )
    assert recent.status_code == 200
    assert [item["title"] for item in recent.json()] == [
        "pet supplies",
        "monthly household list",
    ]
    assert recent.json()[0]["is_active"] is True
    assert recent.json()[1]["is_active"] is False

    searched = await conversation_client.get(
        "/api/conversations?query=monthly", headers=headers("history-user")
    )
    assert [item["id"] for item in searched.json()] == [first.json()["id"]]

    reopened = await conversation_client.post(
        f"/api/conversations/{first.json()['id']}/open",
        headers=headers("history-user"),
    )
    assert reopened.status_code == 200
    assert reopened.json()["submitted_query"] == "monthly household list"

    current = await conversation_client.get(
        "/api/conversations/current", headers=headers("history-user")
    )
    assert current.json()["id"] == first.json()["id"]
    assert current.json()["id"] != second.json()["id"]


@pytest.mark.asyncio
async def test_user_cannot_open_another_users_chat(
    conversation_client: AsyncClient,
) -> None:
    saved = await conversation_client.put(
        "/api/conversations/current",
        headers=headers("owner"),
        json=state("private basket"),
    )
    response = await conversation_client.post(
        f"/api/conversations/{saved.json()['id']}/open",
        headers=headers("intruder"),
    )
    assert response.status_code == 404


def voice_turns(count: int, *, content: str = "") -> list[dict[str, str]]:
    return [
        {
            "id": f"voice-{index}",
            "role": "user" if index % 2 == 0 else "assistant",
            "content": content or f"voice turn {index}",
            "modality": "voice",
            "created_at": "2026-11-22T10:00:00Z",
        }
        for index in range(count)
    ]


@pytest.mark.asyncio
async def test_long_voice_session_is_saved_and_restored(
    conversation_client: AsyncClient,
) -> None:
    turns = voice_turns(300)
    turns[0]["content"] = "डियोडोरेंट " * 1500  # one long merged spoken turn
    saved = await conversation_client.put(
        "/api/conversations/current",
        headers=headers("long-voice-user"),
        json={"submitted_query": "deodorant", "products": [], "searched": False, "messages": turns},
    )
    assert saved.status_code == 200, saved.text

    restored = await conversation_client.get(
        "/api/conversations/current", headers=headers("long-voice-user")
    )
    assert len(restored.json()["messages"]) == 300
    assert restored.json()["messages"][0]["content"] == turns[0]["content"].strip()


@pytest.mark.asyncio
async def test_voice_chat_reopens_from_drawer_with_its_turns_and_sorts_first(
    conversation_client: AsyncClient,
) -> None:
    user = headers("drawer-voice-user")
    voice = await conversation_client.put(
        "/api/conversations/current",
        headers=user,
        json={"submitted_query": "", "products": [], "searched": False, "messages": voice_turns(4)},
    )
    await conversation_client.post("/api/conversations/new", headers=user)
    await conversation_client.put("/api/conversations/current", headers=user, json=state("tea"))

    listed = (await conversation_client.get("/api/conversations", headers=user)).json()
    assert [item["title"] for item in listed] == ["tea", "voice turn 0"]
    assert listed[1]["preview"] == "voice turn 3"

    reopened = await conversation_client.post(
        f"/api/conversations/{voice.json()['id']}/open", headers=user
    )
    assert [item["content"] for item in reopened.json()["messages"]] == [
        f"voice turn {index}" for index in range(4)
    ]
    relisted = (await conversation_client.get("/api/conversations", headers=user)).json()
    assert relisted[0]["id"] == voice.json()["id"]
    assert relisted[0]["is_active"] is True
    assert [item["is_active"] for item in relisted] == [True, False]

    after_reload = await conversation_client.get("/api/conversations/current", headers=user)
    assert after_reload.json()["id"] == voice.json()["id"]
    assert len(after_reload.json()["messages"]) == 4
