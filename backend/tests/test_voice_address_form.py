import json
from typing import Any

import pytest

from app.commerce.zepto.errors import ZeptoConfigurationError
from app.commerce.zepto.models import ConnectionStatus
from app.voice.address_tools import (
    ADDRESS_TOOL_DEFINITIONS,
    OPEN_ADDRESS_FORM,
    TEXT_LIMITS,
)
from app.voice.sideband import SessionChannel, SidebandRunner
from app.voice.toolbox import VOICE_TOOL_DEFINITIONS, VoiceToolbox
from tests.shopping_fakes import World
from tests.test_voice_tools import FakeConnection, nested

NULL_FIELDS: dict[str, Any] = {"store": "unspecified", "address_type": None, **dict.fromkeys(TEXT_LIMITS)}


class NoSearch:
    async def search(self, query: str, address_id: str, offset: int):
        raise AssertionError("address form must not search")


class FakeZepto:
    """Connection status only; any provider method use would fail loudly."""

    def __init__(self, status: ConnectionStatus | Exception) -> None:
        self.status = status
        self.calls = 0

    async def connection_status(self) -> ConnectionStatus:
        self.calls += 1
        if isinstance(self.status, Exception):
            raise self.status
        return self.status

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        raise AssertionError(f"address form must not call Zepto.{name}")


LIVE = ConnectionStatus(connected=True, mode="live")


def toolbox(zepto: FakeZepto | None) -> VoiceToolbox:
    world = World()
    return VoiceToolbox(
        NoSearch(), world.baskets, world.approvals, "addr_home",
        household_id="user_test_voice", zepto_connection=zepto,
    )


async def call(box: VoiceToolbox, **arguments: Any) -> dict[str, Any]:
    outcome = await box.execute(OPEN_ADDRESS_FORM, json.dumps({**NULL_FIELDS, **arguments}))
    return outcome.model_dump()


def test_tool_is_strict_text_only_and_registered() -> None:
    definition = ADDRESS_TOOL_DEFINITIONS[0]
    assert definition in VOICE_TOOL_DEFINITIONS
    assert definition["strict"] is True
    parameters = definition["parameters"]
    assert parameters["additionalProperties"] is False
    assert set(parameters["required"]) == set(parameters["properties"])
    forbidden = {"latitude", "longitude", "contact_number", "phone"}
    assert forbidden.isdisjoint(parameters["properties"])


async def test_opens_zepto_form_with_cleaned_prefill_and_saves_nothing() -> None:
    zepto = FakeZepto(LIVE)
    result = await call(
        toolbox(zepto),
        address_type="HOME",
        label="  Naya   ghar ",
        flat_details="Flat 4B\u0000\n",
        building_name="Lake View Apartments",
        landmark="",
        contact_name="Riya",
    )

    assert zepto.calls == 1
    assert result["model_output"]["status"] == "form_opened"
    assert "Nothing was saved" in result["model_output"]["message"]
    assert result["ui_event"] == {
        "type": "address_form.open",
        "provider": "zepto",
        "prefill": {
            "type": "HOME",
            "name": "Naya ghar",
            "flat_details": "Flat 4B",
            "building_name": "Lake View Apartments",
            "contact_name": "Riya",
        },
    }


async def test_hindi_text_is_preserved() -> None:
    result = await call(toolbox(FakeZepto(LIVE)), landmark="मंदिर के पास")
    assert result["ui_event"]["prefill"] == {"landmark": "मंदिर के पास"}


async def test_empty_request_still_opens_blank_form() -> None:
    result = await call(toolbox(FakeZepto(LIVE)))
    assert result["model_output"]["status"] == "form_opened"
    assert result["ui_event"]["prefill"] == {}


@pytest.mark.parametrize(
    "arguments",
    [
        {"latitude": 12.97},
        {"longitude": 77.59},
        {"contact_number": "9876543210"},
        {"label": "x" * (TEXT_LIMITS["label"] + 1)},
        {"formatted_address": "x" * (TEXT_LIMITS["formatted_address"] + 1)},
        {"address_type": "VILLA"},
        {"store": "blinkit"},
        {"label": 42},
    ],
)
async def test_rejects_coordinates_phone_and_invalid_fields(arguments: dict[str, Any]) -> None:
    zepto = FakeZepto(LIVE)
    result = await call(toolbox(zepto), **arguments)
    assert result["model_output"]["status"] == "error"
    assert result["ui_event"] is None
    assert zepto.calls == 0


async def test_rejects_malformed_json() -> None:
    outcome = await toolbox(FakeZepto(LIVE)).execute(OPEN_ADDRESS_FORM, "{not json")
    assert outcome.model_output["status"] == "error"
    assert outcome.ui_event is None


async def test_swiggy_request_is_redirected_to_swiggy_app() -> None:
    zepto = FakeZepto(LIVE)
    result = await call(toolbox(zepto), store="swiggy_instamart", flat_details="12")
    assert result["model_output"]["status"] == "swiggy_unsupported"
    assert "Swiggy app" in result["model_output"]["message"]
    assert result["ui_event"] == {"type": "address_form.unavailable", "reason": "swiggy_unsupported"}
    assert zepto.calls == 0


@pytest.mark.parametrize(
    ("zepto", "status", "reason"),
    [
        (None, "not_connected", "zepto_not_connected"),
        (FakeZepto(ConnectionStatus(connected=False)), "not_connected", "zepto_not_connected"),
        (FakeZepto(ZeptoConfigurationError("token store unreadable")), "not_connected", "zepto_not_connected"),
        (FakeZepto(ConnectionStatus(connected=True, mode="demo")), "demo_mode", "demo_mode"),
    ],
)
async def test_unavailable_zepto_never_opens_form(
    zepto: FakeZepto | None, status: str, reason: str
) -> None:
    result = await call(toolbox(zepto), flat_details="Flat 4B")
    assert result["model_output"]["status"] == status
    assert result["ui_event"] == {"type": "address_form.unavailable", "reason": reason}


async def test_sideband_publishes_form_event_and_returns_review_status() -> None:
    connection = FakeConnection(
        [
            nested(
                "del_1",
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "call_id": "call_addr",
                        "name": OPEN_ADDRESS_FORM,
                        "arguments": json.dumps({**NULL_FIELDS, "building_name": "Green Park"}),
                    },
                },
                "e1",
            ),
            nested("del_1", {"type": "response.completed"}, "e2"),
        ]
    )
    channel = SessionChannel()
    await SidebandRunner(connection, toolbox(FakeZepto(LIVE)), channel.publish).run()  # type: ignore[arg-type]

    assert channel.history == [
        {"type": "tool.started", "name": OPEN_ADDRESS_FORM},
        {"type": "address_form.open", "provider": "zepto", "prefill": {"building_name": "Green Park"}},
    ]
    output = json.loads(connection.sent[0]["item"]["output"])
    assert output["status"] == "form_opened"
    assert connection.sent[1]["type"] == "response.create"


async def test_live_session_wires_zepto_connection_into_address_tool() -> None:
    import httpx

    from app.voice.service import LiveSessionService
    from tests.test_voice import OFFER, RecordingHub, settings

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"session": {"id": "live_addr"}, "transport": {"sdp": "answer"}})

    world = World()
    hub = RecordingHub()
    zepto = FakeZepto(LIVE)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await LiveSessionService(
            settings(), NoSearch(), hub, client,  # type: ignore[arg-type]
            baskets=world.baskets, approvals=world.approvals, zepto_connection=zepto,
        ).create(OFFER, "addr_home", household_id="user_test_voice")

    executor = hub.started[0][1]
    outcome = await executor.execute(OPEN_ADDRESS_FORM, json.dumps(NULL_FIELDS))
    assert outcome.model_output["status"] == "form_opened"
    assert zepto.calls == 1
