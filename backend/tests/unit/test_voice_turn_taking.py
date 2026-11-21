import json

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.voice.prompts import LIVE_INSTRUCTIONS
from app.voice.service import LiveSessionService, build_session_config
from app.voice.turn_taking import TURN_TAKING_POLICY, YIELD_INSTRUCTION, client_turn_taking


OFFER = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
# Session fields documented for POST /v1/live/sessions (MediaSessionConfig).
DOCUMENTED_SESSION_FIELDS = {
    "model", "audio", "client", "delegation", "idle_timeout_s", "input",
    "instructions", "safety_identifier", "store", "triggers", "wake_policy",
}


def settings(mode: str = "native") -> Settings:
    return Settings(_env_file=None, openai_api_key=SecretStr("sk-test"), voice_turn_taking=mode)


class Hub:
    def start(self, session_id, executor) -> None:  # noqa: ANN001
        pass


class NoSearch:
    async def search(self, query, address_id, offset):  # noqa: ANN001
        raise AssertionError("search must not run here")


def test_default_mode_is_native_and_keeps_current_instructions() -> None:
    config = build_session_config(Settings(_env_file=None))
    assert config["instructions"] == LIVE_INSTRUCTIONS
    assert Settings(_env_file=None).voice_turn_taking == "native"


@pytest.mark.parametrize("mode", ["native", "tuned", "smart_turn"])
def test_every_mode_sends_only_documented_live_fields(mode: str) -> None:
    config = build_session_config(settings(mode))
    assert set(config) <= DOCUMENTED_SESSION_FIELDS
    assert "turn_detection" not in json.dumps(config)
    assert "audio" in config and set(config["audio"]) == {"output"}


@pytest.mark.parametrize("mode", ["tuned", "smart_turn"])
def test_tuned_modes_append_turn_policy_without_rewriting_base(mode: str) -> None:
    instructions = build_session_config(settings(mode))["instructions"]
    assert instructions.startswith(LIVE_INSTRUCTIONS.rstrip("\n"))
    assert TURN_TAKING_POLICY.strip() in instructions
    assert "Interruption policy:" in instructions


def test_turn_policy_has_no_masculine_self_reference() -> None:
    text = TURN_TAKING_POLICY + YIELD_INSTRUCTION
    for masculine in ("karunga", "raha hoon", "karta hoon", "samajh gaya"):
        assert masculine not in text


def test_client_turn_taking_only_ships_yield_instruction_for_smart_turn() -> None:
    assert client_turn_taking("native") == {"mode": "native"}
    assert client_turn_taking("tuned") == {"mode": "tuned"}
    assert client_turn_taking("smart_turn") == {"mode": "smart_turn", "yield_instruction": YIELD_INSTRUCTION}


def test_unknown_mode_is_rejected() -> None:
    with pytest.raises(ValueError):
        settings("semantic_vad")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["native", "tuned", "smart_turn"])
async def test_session_response_reports_turn_taking_mode(mode: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"session": {"id": "live_1"}, "transport": {"sdp": "answer"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await LiveSessionService(settings(mode), NoSearch(), Hub(), client).create(OFFER)  # type: ignore[arg-type]

    assert result.turn_taking["mode"] == mode
