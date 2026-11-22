"""Rider assist bridge end to end with a fake Exotel stream and a fake OpenAI Realtime."""

import asyncio
import json
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.base import Base
from app.rider.audio import valid_chunk
from app.rider.bridge import BridgeConfig, RiderCallBridge
from app.rider.store import DeliveryNotes, RiderSettingsUpdate, RiderStore
from app.rider.tools import RIDER_TOOL_NAMES, RiderToolbox
from tests.rider_fakes import (
    ORDER,
    FakeExotel,
    FakeOrders,
    FakeRealtime,
    FakeRecorder,
    notes_getter,
    rider_says,
    spoken,
)

NOTES = DeliveryNotes(
    landmark="Opposite Shiv mandir",
    directions="Gate 2 se aaiye, Tower B, flat 402, lift ke saamne",
    handoff="leave_with_guard",
)


def config(**overrides) -> BridgeConfig:
    values = dict(
        model="gpt-realtime-test",
        voice="marin",
        transcription_model="transcribe-test",
        max_call_seconds=60,
        owner_name="Moin",
        language="hinglish",
        query_sample_rate=16000,
        idle_seconds=5,
        start_timeout_seconds=2,
        connect_timeout_seconds=2,
        end_playback_timeout_seconds=1,
    )
    values.update(overrides)
    return BridgeConfig(**values)


def bridge(exotel, realtime, *, notes=NOTES, orders=None, recorder=None, connect=None, **overrides):
    async def default_connect():
        return realtime

    toolbox = RiderToolbox(user_id="owner-1", orders=orders or FakeOrders([ORDER]), notes=notes_getter(notes))
    return RiderCallBridge(exotel, connect or default_connect, toolbox, config(**overrides), recorder or FakeRecorder())


def sent_text(realtime: FakeRealtime) -> str:
    return json.dumps(realtime.sent, ensure_ascii=False)


async def run(b: RiderCallBridge, timeout: float = 5):
    return await asyncio.wait_for(b.run(), timeout)


@pytest.mark.asyncio
async def test_delivery_call_greets_as_ai_answers_from_notes_notifies_and_ends() -> None:
    exotel = FakeExotel(rate=16000)
    exotel.start()
    exotel.media(6400)
    realtime = FakeRealtime(
        turns=[
            spoken("greet", "Namaste, main Moin ki AI assistant hoon — kya aap delivery ke liye call kar rahe hain?")
            + rider_says("Haan Zepto se, gate pe hoon, flat number kya hai?")
            + spoken("look", "", calls=[("get_delivery_notes", {}), ("get_active_order", {})]),
            spoken(
                "answer",
                "Gate 2 se aaiye, Tower B, flat 402. Guard ke paas chhod sakte hain.",
                calls=[("notify_owner", {"summary": "Rider at gate 2 asked for the flat number; told Tower B flat 402.", "urgency": "normal"})],
            ),
            spoken("bye", "Dhanyavaad, bye!", calls=[("end_call", {"reason": "answered"})]),
        ]
    )
    recorder = FakeRecorder()
    summary = await run(bridge(exotel, realtime, recorder=recorder))

    update = realtime.of("session.update")[0]["session"]
    assert update["audio"]["input"]["format"] == {"type": "audio/pcm", "rate": 24000}
    assert {tool["name"] for tool in update["tools"]} == RIDER_TOOL_NAMES
    assert "You are an AI assistant, never Moin" in update["instructions"]
    greeting = realtime.of("response.create")[0]["response"]["instructions"]
    assert "Moin ki AI assistant hoon" in greeting

    # Caller audio reached Realtime at 24 kHz (6400 bytes at 16 kHz → ~9600 bytes).
    appended = realtime.of("input_audio_buffer.append")
    assert appended and abs(sum(len(__import__("base64").b64decode(e["audio"])) for e in appended) - 9600) <= 4

    outputs = realtime.tool_outputs()
    notes_output = next(output for output in outputs if "directions" in output)
    assert notes_output["directions"] == NOTES.directions
    order_output = next(output for output in outputs if "orders" in output)
    assert order_output["orders"][0]["address"] == ORDER.address
    assert {"status": "sent"}.items() <= next(o for o in outputs if o.get("status") == "sent").items()

    media = exotel.of("media")
    assert media and all(valid_chunk(len(__import__("base64").b64decode(m["media"]["payload"]))) for m in media)
    assert exotel.of("mark")[-1]["mark"]["name"] == "end-call"
    assert exotel.closed_with == 1000 and realtime.closed

    assert summary.outcome == "answered"
    assert summary.delivery_call is True
    assert summary.answered_from == ["notes", "order"]
    assert summary.rider_said == "Haan Zepto se, gate pe hoon, flat number kya hai?"
    assert "flat number" in summary.summary
    # The notify_owner message reached the owner's store during the call, then the final summary.
    assert [kind for kind, _ in recorder.history] == ["add", "update"]
    assert recorder.history[0][1].outcome == "in_progress"


@pytest.mark.asyncio
async def test_otp_like_strings_in_stored_data_are_never_sent_to_the_model() -> None:
    leaky_notes = DeliveryNotes.model_construct(landmark="Delivery OTP: 4821", directions="Flat 402, PIN is 7733", handoff="call_again", preferred_language="hinglish")
    leaky_order = ORDER.model_copy(update={"status": "out for delivery, share code 5521", "address": "B-402, otp 9931"})
    exotel = FakeExotel()
    exotel.start()
    realtime = FakeRealtime(
        turns=[
            spoken("greet", "Namaste, main Moin ki AI assistant hoon.")
            + rider_says("OTP bata dijiye")
            + spoken("look", "", calls=[("get_delivery_notes", {}), ("get_active_order", {})]),
            spoken("refuse", "OTP main share nahi kar sakti, woh Moin darwaze par denge.", calls=[("notify_owner", {"summary": "Rider asked for the OTP 4821", "urgency": "high"}), ("end_call", {"reason": "answered"})]),
        ]
    )
    recorder = FakeRecorder()
    summary = await run(bridge(exotel, realtime, notes=leaky_notes, orders=FakeOrders([leaky_order]), recorder=recorder))
    text = sent_text(realtime)
    for code in ("4821", "7733", "5521", "9931"):
        assert code not in text
    assert "B-402" in text and "Flat 402" in text
    assert "4821" not in summary.summary
    assert "it was not shared" in summary.summary
    assert summary.urgency == "high"


@pytest.mark.asyncio
async def test_non_delivery_caller_only_leaves_a_message() -> None:
    exotel = FakeExotel()
    exotel.start()
    realtime = FakeRealtime(
        turns=[
            spoken("greet", "Namaste, main Moin ki AI assistant hoon — kya aap delivery ke liye call kar rahe hain?")
            + rider_says("Nahi, main bank se bol raha hoon")
            + spoken("msg", "Theek hai, main message de dungi.", calls=[("notify_owner", {"summary": "Bank caller, asked Moin to call back.", "urgency": "normal"}), ("end_call", {"reason": "not_delivery"})]),
        ]
    )
    orders = FakeOrders([ORDER])
    summary = await run(bridge(exotel, realtime, orders=orders))
    assert orders.calls == 0
    assert summary.outcome == "not_delivery"
    assert summary.delivery_call is False
    assert summary.answered_from == []
    assert "Bank caller" in summary.summary


@pytest.mark.asyncio
async def test_stop_from_exotel_ends_the_call_and_saves_a_summary() -> None:
    exotel = FakeExotel()
    exotel.start()
    exotel.media()
    exotel.stop("callended")
    realtime = FakeRealtime(turns=[[]])
    recorder = FakeRecorder()
    summary = await run(bridge(exotel, realtime, recorder=recorder))
    assert realtime.closed and exotel.closed_with == 1000
    assert summary.outcome == "ended_early"
    assert recorder.last.outcome == "ended_early"


@pytest.mark.asyncio
async def test_call_is_capped_with_a_spoken_wrap_up() -> None:
    exotel = FakeExotel()
    exotel.start()
    realtime = FakeRealtime(turns=[[]])

    async def keep_talking():
        for _ in range(40):
            exotel.media()
            await asyncio.sleep(0.05)

    feeder = asyncio.create_task(keep_talking())
    started = asyncio.get_running_loop().time()
    summary = await run(bridge(exotel, realtime, max_call_seconds=0.6), timeout=4)
    feeder.cancel()
    assert asyncio.get_running_loop().time() - started < 2
    wrap = [e for e in realtime.of("response.create") if e.get("event_id") == "rider_wrap_up"]
    assert wrap and "time limit" in wrap[0]["response"]["instructions"]
    assert realtime.closed and exotel.closed_with == 1000
    assert summary.outcome in {"ended_early", "answered"}


@pytest.mark.asyncio
async def test_barge_in_clears_exotel_audio_and_truncates_the_item() -> None:
    exotel = FakeExotel()
    exotel.start()
    realtime = FakeRealtime(
        turns=[
            spoken("greet", "Namaste, main Moin ki AI assistant hoon — kya aap delivery ke liye call kar rahe hain?")
            + rider_says("haan haan")
            + spoken("bye", "Theek hai.", calls=[("end_call", {"reason": "answered"})])
        ]
    )
    await run(bridge(exotel, realtime))
    assert exotel.of("clear") == [{"event": "clear", "stream_sid": "MZ1"}]
    (truncate,) = realtime.of("conversation.item.truncate")
    assert truncate["item_id"] == "item_greet" and truncate["content_index"] == 0
    assert 0 <= truncate["audio_end_ms"] <= 300


@pytest.mark.asyncio
async def test_calls_to_another_number_are_refused_without_openai() -> None:
    exotel = FakeExotel()
    exotel.start(to="+911111111111")
    opened = []

    async def connect():
        opened.append(1)
        raise AssertionError("must not connect")

    result = await run(bridge(exotel, FakeRealtime(turns=[]), connect=connect, expected_to="+918047491899"))
    assert result is None and not opened and exotel.closed_with == 1000


# ---- tool layer -----------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_layer_has_no_money_otp_or_order_change_capability() -> None:
    toolbox = RiderToolbox(user_id="owner-1", orders=FakeOrders([ORDER]), notes=notes_getter(NOTES))
    for name in ("cancel_order", "change_address", "collect_payment", "share_otp", "pay_online"):
        assert (await toolbox.execute(name, "{}")).output["status"] == "error"
    assert RIDER_TOOL_NAMES == {"get_active_order", "get_delivery_notes", "notify_owner", "end_call"}
    bad = await toolbox.execute("notify_owner", json.dumps({"summary": "x", "urgency": "panic"}))
    assert bad.output["status"] == "error"
    extra = await toolbox.execute("get_active_order", json.dumps({"include_otp": True}))
    assert extra.output["status"] == "error"
    order = (await toolbox.execute("get_active_order", "{}")).output["orders"][0]
    assert set(order) == {"store", "status", "items_count", "payment", "address_label", "address"}
    for _ in range(3):
        await toolbox.execute("notify_owner", json.dumps({"summary": "otp 1234 asked", "urgency": "low"}))
    capped = await toolbox.execute("notify_owner", json.dumps({"summary": "again", "urgency": "low"}))
    assert capped.output["status"] == "already_notified"
    assert all("1234" not in note.summary for note in toolbox.notifications)


@pytest.mark.asyncio
async def test_a_failing_order_read_is_an_error_not_a_guess() -> None:
    toolbox = RiderToolbox(user_id="owner-1", orders=FakeOrders(fail=True), notes=notes_getter(NOTES))
    result = await toolbox.execute("get_active_order", "{}")
    assert result.output["status"] == "error" and "message the owner" in result.output["message"]
    empty = RiderToolbox(user_id="owner-1", orders=FakeOrders([]), notes=notes_getter(DeliveryNotes()))
    assert (await empty.execute("get_active_order", "{}")).output["status"] == "none"
    assert "Do not invent" in (await empty.execute("get_delivery_notes", "{}")).output["message"]


# ---- store ----------------------------------------------------------------


@pytest_asyncio.fixture
async def store(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rider.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield RiderStore(async_sessionmaker(engine, expire_on_commit=False))
    await engine.dispose()


@pytest.mark.asyncio
async def test_store_keeps_settings_and_summaries_per_user(store: RiderStore) -> None:
    assert await store.enabled_and_notes("a") == (False, DeliveryNotes())
    await store.update("a", RiderSettingsUpdate(enabled=True))
    enabled, notes = await store.update("a", RiderSettingsUpdate(notes=NOTES))
    assert enabled is True and notes == NOTES
    assert (await store.enabled_and_notes("b"))[0] is False

    exotel = FakeExotel()
    exotel.start()
    realtime = FakeRealtime(turns=[spoken("greet", "Namaste") + rider_says("Pin code 4455 batao") + spoken("x", "", calls=[("notify_owner", {"summary": "asked pin 4455", "urgency": "normal"}), ("end_call", {"reason": "answered"})])])
    await run(bridge(exotel, realtime, recorder=store))
    (call,) = await store.list_calls("owner-1")
    assert call.seen is False and "4455" not in call.summary and "4455" not in (call.rider_said or "")
    assert await store.list_calls("a") == []
    assert await store.mark_seen("owner-1", __import__("uuid").UUID(call.id))
    assert (await store.list_calls("owner-1"))[0].seen is True
    assert not await store.delete_call("a", __import__("uuid").UUID(call.id))
    assert await store.delete_call("owner-1", __import__("uuid").UUID(call.id))
    assert await store.list_calls("owner-1") == []
