"""POST /api/chat/turn on the real app: demo commerce, PostgreSQL baskets, a fake model."""

import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.chat.service import ChatTurnService
from app.commerce.demo import DEMO_ADDRESS_ID
from app.dependencies import get_chat_service, get_voice_service
from tests.integration.helpers import as_user


class ScriptedModel:
    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.requests.append(kwargs)
        step = self.script.pop(0)
        if isinstance(step, str):
            return SimpleNamespace(id=f"r{len(self.requests)}", output=[], output_text=step)
        calls = [
            SimpleNamespace(type="function_call", name=name, arguments=json.dumps(arguments), call_id=f"c{index}")
            for index, (name, arguments) in enumerate(step)
        ]
        return SimpleNamespace(id=f"r{len(self.requests)}", output=calls, output_text="")


@pytest.fixture
def model(override) -> ScriptedModel:
    scripted = ScriptedModel([])
    service = ChatTurnService(get_voice_service().settings, get_voice_service(), responses=scripted)
    override(get_chat_service, lambda: service)
    return scripted


def test_typed_turn_searches_adds_and_persists_the_basket(api: TestClient, model: ScriptedModel) -> None:
    alice = as_user("alice")
    model.script = [
        [("search_products", {"query": "Fortune sunflower oil 1 litre"})],
        [("add_to_basket", {"option_id": "opt1", "quantity": 2, "store_id": None})],
        "Fortune Sunflower Oil 1 L, do packet, Instamart basket mein add ho gaye.",
    ]

    response = api.post(
        "/api/chat/turn",
        headers=alice,
        json={"message": "Fortune tel 1 litre do packet add karo", "swiggy_address_id": DEMO_ADDRESS_ID, "zepto_address_id": DEMO_ADDRESS_ID},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reply"].startswith("Fortune Sunflower Oil")
    assert body["conversation_id"] is None
    assert [event["type"] for event in body["ui_events"]] == ["tool.results", "basket.updated"]
    results, updated = body["ui_events"]
    assert {product["provider"] for product in results["products"]} == {"swiggy_instamart", "zepto"}
    assert updated["provider"] == "swiggy_instamart"
    basket = api.get("/api/basket", headers=alice, params={"provider": "swiggy_instamart"}).json()
    assert [(line["name"], line["quantity"]) for line in basket["lines"]] == [("Fortune Sunflower Oil", 2)]
    assert api.get("/api/basket", headers=as_user("bob"), params={"provider": "swiggy_instamart"}).json()["lines"] == []


def test_saved_conversation_turns_are_sent_as_context(api: TestClient, model: ScriptedModel) -> None:
    alice = as_user("alice")
    saved = api.put(
        "/api/conversations/current",
        headers=alice,
        json={
            "messages": [
                {"id": "u1", "role": "user", "content": "deo chahiye", "modality": "text", "created_at": "2026-11-21T10:00:00Z"},
                {"id": "a1", "role": "assistant", "content": "Kaunsa brand aur size?", "modality": "voice", "created_at": "2026-11-21T10:00:01Z"},
                {"id": "u2", "role": "user", "content": "AXE Dark", "modality": "text", "created_at": "2026-11-21T10:00:02Z"},
            ]
        },
    ).json()
    model.script = ["Kaunsa size chahiye?"]

    response = api.post("/api/chat/turn", headers=alice, json={"conversation_id": saved["id"], "message": "AXE Dark"})

    assert response.status_code == 200, response.text
    assert response.json()["conversation_id"] == saved["id"]
    # The shared loader (the one voice uses) puts the saved turns in the instructions;
    # the already-saved current message is the input, not a repeated turn.
    instructions = model.requests[0]["instructions"]
    assert '- User: "deo chahiye"' in instructions
    assert '- Assistant: "Kaunsa brand aur size?"' in instructions
    assert '- User: "AXE Dark"' not in instructions
    assert model.requests[0]["input"] == [{"role": "user", "content": "AXE Dark"}]


def test_an_older_chat_does_not_get_the_current_chats_turns(api: TestClient, model: ScriptedModel) -> None:
    alice = as_user("alice")
    first = api.put(
        "/api/conversations/current",
        headers=alice,
        json={"messages": [{"id": "u1", "role": "user", "content": "atta chahiye", "modality": "text", "created_at": "2026-11-21T10:00:00Z"}]},
    ).json()
    assert api.post("/api/conversations/new", headers=alice).status_code in {200, 204}
    api.put(
        "/api/conversations/current",
        headers=alice,
        json={"messages": [{"id": "u2", "role": "user", "content": "makhana pack zaroori", "modality": "text", "created_at": "2026-11-21T11:00:00Z"}]},
    )
    model.script = ["ok"]

    response = api.post("/api/chat/turn", headers=alice, json={"conversation_id": first["id"], "message": "hello"})

    assert response.status_code == 200, response.text
    assert "makhana pack zaroori" not in model.requests[0]["instructions"].split("# Recent chats")[0]


def test_another_users_conversation_is_not_found(api: TestClient, model: ScriptedModel) -> None:
    saved = api.put(
        "/api/conversations/current",
        headers=as_user("alice"),
        json={"messages": [{"id": "u1", "role": "user", "content": "private", "modality": "text", "created_at": "2026-11-21T10:00:00Z"}]},
    ).json()

    response = api.post("/api/chat/turn", headers=as_user("bob"), json={"conversation_id": saved["id"], "message": "hi"})

    assert response.status_code == 404
    assert model.requests == []


@pytest.mark.parametrize(
    "body",
    [{"message": "   "}, {"message": "x" * 2001}, {"message": "hi", "user_id": "someone"}, {}],
)
def test_invalid_requests_are_rejected_before_the_model(api: TestClient, model: ScriptedModel, body: dict) -> None:
    assert api.post("/api/chat/turn", headers=as_user("alice"), json=body).status_code == 422
    assert model.requests == []


def test_without_an_api_key_the_endpoint_says_so(api: TestClient) -> None:
    response = api.post("/api/chat/turn", headers=as_user("alice"), json={"message": "atta chahiye"})

    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.json()["detail"]
