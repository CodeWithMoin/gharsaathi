import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings
from app.dependencies import get_voice_service
from app.main import app
from app.voice.service import (
    LiveSession,
    LiveSessionService,
    VoiceConfigurationError,
    VoiceProviderError,
)
from app.voice.prompts import BACKEND_INSTRUCTIONS, LIVE_INSTRUCTIONS
from app.voice.tools import MultiProviderShoppingToolExecutor, ShoppingToolExecutor
from tests.shopping_fakes import World


class RecordingHub:
    def __init__(self) -> None:
        self.started: list[tuple[str, ShoppingToolExecutor]] = []
        self.owners: list[tuple[str, str]] = []

    def start(
        self, session_id: str, executor: ShoppingToolExecutor, *, owner_id: str, events_token: str
    ) -> None:
        self.started.append((session_id, executor))
        self.owners.append((owner_id, events_token))


class NoSearch:
    async def search(self, query: str, address_id: str, offset: int):
        raise AssertionError("search must not run here")


def service(app_settings: Settings, client: httpx.AsyncClient, hub: RecordingHub | None = None) -> LiveSessionService:
    return LiveSessionService(app_settings, NoSearch(), hub or RecordingHub(), client)  # type: ignore[arg-type]


OFFER = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"


def settings(api_key: str = "sk-test-key") -> Settings:
    return Settings(_env_file=None, openai_api_key=SecretStr(api_key))


def test_voice_prompt_uses_gharsaathi_and_feminine_hinglish() -> None:
    combined = "\n".join((LIVE_INSTRUCTIONS, BACKEND_INSTRUCTIONS))
    assert "GharSaathi" in combined
    assert "GharCart" not in combined
    assert "dekh rahi hoon" in combined
    assert "Never use masculine" in combined
    assert "user always speaks first" in combined


@pytest.mark.asyncio
async def test_create_posts_client_delegation_session_and_returns_answer() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            201,
            json={
                "session": {"id": "live_123"},
                "transport": {"type": "webrtc", "sdp": "answer-sdp"},
            },
        )

    hub = RecordingHub()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await service(settings(), client, hub).create(
            OFFER, "addr_home", household_id="test-user"
        )

    assert (result.session_id, result.sdp) == ("live_123", "answer-sdp")
    assert len(result.events_token) >= 32
    # The events token is bound to the verified user who created the session.
    assert hub.owners == [("test-user", result.events_token)]
    assert hub.started[0][0] == "live_123"
    assert hub.started[0][1].address_id == "addr_home"
    request = seen[0]
    assert str(request.url) == "https://api.openai.com/v1/live/sessions"
    assert request.headers["authorization"] == "Bearer sk-test-key"
    body = json.loads(request.content)
    assert body["transport"] == {"type": "webrtc", "sdp": OFFER}
    session = body["session"]
    assert session["model"] == "gpt-live-1.1"
    delegation = session["delegation"]
    assert delegation["type"] == "responses"
    assert delegation["responses"]["model"] == "gpt-6-luna"
    assert [tool["name"] for tool in delegation["responses"]["tools"]] == ["search_products"]
    assert session["audio"] == {"output": {"voice": "nira"}}
    assert "format" not in session["audio"]
    assert "input" not in session


@pytest.mark.asyncio
async def test_basket_enabled_session_exposes_only_safe_basket_tools() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            201,
            json={"session": {"id": "live_basket"}, "transport": {"type": "webrtc", "sdp": "answer"}},
        )

    world = World()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await LiveSessionService(
            settings(), NoSearch(), RecordingHub(), client,
            baskets=world.baskets, approvals=world.approvals,
        ).create(OFFER, "addr_home", household_id="test-user")

    tools = json.loads(seen[0].content)["session"]["delegation"]["responses"]["tools"]
    names = {tool["name"] for tool in tools}
    assert names == {
        "search_products", "add_to_basket", "set_basket_quantity", "view_basket", "request_approval",
        "open_address_form", "recall_household_preferences", "remember_preference",
        "update_preference", "forget_preference", "list_preferences", "confirm_memory",
        "prepare_order", "list_delivery_addresses", "select_delivery_address",
        "read_attached_photo",
    }
    # prepare_order only shows the order card; no tool can approve, check out or order.
    assert all("checkout" not in name and "approve" not in name for name in names)
    assert [name for name in names if "order" in name] == ["prepare_order"]
    prepare = next(tool for tool in tools if tool["name"] == "prepare_order")
    assert "never approves or places an order" in prepare["description"]


@pytest.mark.asyncio
async def test_session_routes_selected_addresses_to_both_read_only_searchers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            json={"session": {"id": "live_multi"}, "transport": {"type": "webrtc", "sdp": "answer"}},
        )

    hub = RecordingHub()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await LiveSessionService(
            settings(),
            NoSearch(),
            hub,
            client,
            zepto_searcher=NoSearch(),
        ).create(OFFER, "swiggy-home", "zepto-home", household_id="test-user")

    executor = hub.started[0][1]
    assert isinstance(executor, MultiProviderShoppingToolExecutor)
    assert [(target.store, target.address_id) for target in executor.targets] == [
        ("Swiggy Instamart", "swiggy-home"),
        ("Zepto", "zepto-home"),
    ]


@pytest.mark.asyncio
async def test_missing_api_key_never_calls_openai() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("OpenAI must not be called without a key")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(VoiceConfigurationError):
            await service(settings(""), client).create(OFFER)


@pytest.mark.asyncio
async def test_openai_error_message_is_surfaced_without_secrets() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"message": "Offer did not have an audio media section."}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(VoiceProviderError) as caught:
            await service(settings(), client).create(OFFER)

    assert caught.value.status_code == 400
    assert "audio media section" in str(caught.value)
    assert "sk-test-key" not in str(caught.value)


class FakeVoiceService:
    async def create(
        self,
        offer_sdp: str,
        swiggy_address_id: str | None = None,
        zepto_address_id: str | None = None,
        household_id: str = "",
        data_channel_id: int | None = None,
    ) -> LiveSession:
        del offer_sdp, swiggy_address_id, zepto_address_id, household_id, data_channel_id
        return LiveSession(session_id="live_fake", sdp="answer", events_token="token-fake")


def api_client() -> TestClient:
    app.dependency_overrides[get_voice_service] = lambda: FakeVoiceService()
    return TestClient(app)


def test_session_endpoint_returns_answer_for_allowed_origin() -> None:
    response = api_client().post(
        "/api/voice/session",
        json={"sdp": OFFER},
        headers={"Origin": "http://localhost:3003"},
    )
    assert response.status_code == 201
    assert response.json() == {
        "session_id": "live_fake",
        "sdp": "answer",
        "turn_taking": {"mode": "native"},
        "events_token": "token-fake",
        "assistant_transcript_script": "roman",
    }


def test_session_endpoint_rejects_unknown_origin() -> None:
    response = api_client().post(
        "/api/voice/session",
        json={"sdp": OFFER},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403


def test_session_endpoint_rejects_offer_without_audio() -> None:
    response = api_client().post(
        "/api/voice/session",
        json={"sdp": "v=0\r\n"},
        headers={"Origin": "http://localhost:3003"},
    )
    assert response.status_code == 422
