"""Rider assist pure logic: Vobiz protocol, signatures, stream tokens, audio, safety, notes."""

import array
import base64
import hashlib
import hmac
import json
import math

import pytest
from pydantic import ValidationError

from app.rider import vobiz
from app.rider.audio import OutboundChunker, Resampler
from app.rider.prompts import greeting, rider_instructions
from app.rider.safety import redact_secrets, redact_store_text, request_flags
from app.rider.store import DeliveryNotes, RiderCallSummary, RiderSettingsUpdate, outcome_text
from app.rider.telephony import MULAW_PLAN, ProtocolError, l16_plan, mask_number, same_number

ADAPTER = vobiz.VobizAdapter()
SECRET = "s" * 40


def _pcm(samples: list[int]) -> bytes:
    return array.array("h", samples).tobytes()


def _samples(data: bytes) -> list[int]:
    values = array.array("h")
    values.frombytes(data)
    return list(values)


# ---- Vobiz stream protocol --------------------------------------------------


def _start(encoding: str = "audio/x-mulaw", rate: int = 8000, extra: str = "{}") -> str:
    return json.dumps({"sequenceNumber": 0, "event": "start", "start": {"callId": "5401fd2e-6344", "streamId": "c4df", "accountId": "500025", "tracks": ["inbound"], "mediaFormat": {"encoding": encoding, "sampleRate": rate}}, "extra_headers": extra})


def test_start_picks_the_audio_plan_from_media_format() -> None:
    event = ADAPTER.parse(_start())
    assert event.kind == "start" and event.start is not None
    assert event.start.call_id == "5401fd2e-6344" and event.start.stream_id == "c4df"
    assert event.start.audio == MULAW_PLAN
    assert MULAW_PLAN.realtime_format == {"type": "audio/pcmu"} and not MULAW_PLAN.resample_inbound
    l16 = ADAPTER.parse(_start("audio/x-l16", 16000)).start.audio
    assert l16 == l16_plan(16000) and l16.resample_inbound and l16.outbound_rate == 24000
    assert l16.realtime_format == {"type": "audio/pcm", "rate": 24000}
    for bad in (_start("audio/x-l16", 24000), _start("audio/x-mulaw", 16000), _start("audio/opus", 8000)):
        with pytest.raises(ProtocolError):
            ADAPTER.parse(bad)


def test_extra_headers_arrive_as_a_non_json_string() -> None:
    event = ADAPTER.parse(_start(extra="{X-VH-agentType: sales, X-VH-k: v}"))
    assert event.start.extra == {"agentType": "sales", "k": "v"}
    assert vobiz.parse_extra_headers("{}") == {}


def test_media_played_cleared_and_other_events() -> None:
    payload = base64.b64encode(b"\x7f" * 160).decode()
    media = ADAPTER.parse(json.dumps({"event": "media", "streamId": "c4df", "media": {"track": "inbound", "timestamp": "1", "chunk": 2, "payload": payload}}))
    assert media.kind == "media" and media.audio == b"\x7f" * 160
    outbound = ADAPTER.parse(json.dumps({"event": "media", "media": {"track": "outbound", "payload": payload}}))
    assert outbound.kind == "ignored"
    assert ADAPTER.parse('{"event":"playedStream","name":"end-call"}').name == "end-call"
    assert ADAPTER.parse('{"event":"clearedAudio","streamId":"c4df"}').kind == "cleared"
    assert ADAPTER.parse('{"event":"dtmf","dtmf":{"digit":"5"}}').kind == "dtmf"
    assert ADAPTER.parse('{"event":"somethingNew"}').kind == "ignored"
    for bad in ("not json", "[]", '{"event":"media","media":{"payload":"***"}}', '{"event":"start"}', '{"event":"start","start":{"callId":"x"}}'):
        with pytest.raises(ProtocolError):
            ADAPTER.parse(bad)


def test_outgoing_commands_follow_the_documented_shape() -> None:
    play = json.loads(ADAPTER.play("c4df", MULAW_PLAN, b"\x55" * 320))
    assert play == {"event": "playAudio", "streamId": "c4df", "media": {"contentType": "audio/x-mulaw", "sampleRate": 8000, "payload": base64.b64encode(b"\x55" * 320).decode()}}
    l16 = json.loads(ADAPTER.play("c4df", l16_plan(16000), b"\x00\x00"))["media"]
    assert l16["contentType"] == "audio/x-l16" and l16["sampleRate"] == 24000
    assert json.loads(ADAPTER.checkpoint("c4df", "end-call")) == {"event": "checkpoint", "streamId": "c4df", "name": "end-call"}
    assert json.loads(ADAPTER.clear("c4df")) == {"event": "clearAudio", "streamId": "c4df"}
    assert json.loads(ADAPTER.stop("c4df")) == {"event": "stop", "streamId": "c4df"}


def test_numbers_are_masked_and_compared_by_last_ten_digits() -> None:
    assert mask_number("+919876543210") == "•••• 210"
    assert mask_number("") == "unknown"
    assert same_number("+918047491899", "08047491899")
    assert not same_number("+918047491899", "+918047491800")


# ---- Vobiz webhooks ---------------------------------------------------------


def _signed(url: str, token: str, nonce: str = "12345678901234567890", version: str = "V3") -> dict[str, str]:
    joiner = "." if version == "V3" else ""
    signature = base64.b64encode(hmac.new(token.encode(), f"{url}{joiner}{nonce}".encode(), hashlib.sha256).digest()).decode()
    return {f"X-Vobiz-Signature-{version}": signature, f"X-Vobiz-Signature-{version}-Nonce": nonce}


def test_webhook_signatures_v2_and_v3_and_fail_closed() -> None:
    url = vobiz.base_url("https://rider.example.in", vobiz.ANSWER_PATH)
    assert url == "https://rider.example.in/api/rider/vobiz/answer"
    assert vobiz.signature_valid(url, "tok", _signed(url, "tok"))
    assert vobiz.signature_valid(url, "tok", _signed(url, "tok", version="V2"))
    assert vobiz.signature_valid(url + "?CallUUID=x", "tok", _signed(url, "tok"))
    assert not vobiz.signature_valid(url, "tok", _signed(url, "other"))
    assert not vobiz.signature_valid(url, "tok", _signed(url + "/x", "tok"))
    assert not vobiz.signature_valid(url, "tok", {})
    assert not vobiz.signature_valid(url, "", _signed(url, ""))
    v1_only = {"X-Vobiz-Signature": "abc"}
    assert not vobiz.signature_valid(url, "tok", v1_only)


def test_stream_tokens_are_bound_to_the_call_and_expire() -> None:
    token = vobiz.stream_token(SECRET, "call-0001-uuid", now=1_800_000_000)
    assert vobiz.stream_token_valid(SECRET, "call-0001-uuid", token, now=1_800_000_060)
    assert not vobiz.stream_token_valid(SECRET, "call-0001-uuid", token, now=1_800_000_000 + vobiz.STREAM_TOKEN_TTL_SECONDS + 1)
    assert not vobiz.stream_token_valid(SECRET, "call-0002-uuid", token, now=1_800_000_060)
    assert not vobiz.stream_token_valid("t" * 40, "call-0001-uuid", token, now=1_800_000_060)
    assert not vobiz.stream_token_valid("short", "call-0001-uuid", token, now=1_800_000_060)
    tampered = token[:-1] + ("1" if token[-1] == "0" else "0")
    assert not vobiz.stream_token_valid(SECRET, "call-0001-uuid", tampered, now=1_800_000_060)
    assert not vobiz.stream_token_valid(SECRET, "../etc", token, now=1_800_000_060)
    url = vobiz.stream_url("https://rider.example.in", SECRET, "call-0001-uuid", now=1_800_000_000)
    assert url == f"wss://rider.example.in/api/rider/stream/call-0001-uuid/{token}"


def test_answer_xml_streams_mulaw_and_redirects_after() -> None:
    xml = vobiz.answer_xml("wss://h/api/rider/stream/c/t", "https://h/api/rider/vobiz/after-stream", timeout_seconds=210)
    assert '<Stream bidirectional="true" keepCallAlive="true" audioTrack="inbound" contentType="audio/x-mulaw;rate=8000" streamTimeout="210">wss://h/api/rider/stream/c/t</Stream>' in xml
    assert '<Redirect method="POST">https://h/api/rider/vobiz/after-stream</Redirect>' in xml
    assert "&amp;" in vobiz.answer_xml("wss://h/s?a=1&b=2", "https://h/a", timeout_seconds=1)
    sorry = vobiz.after_stream_xml(apologise=True, language="hinglish")
    assert '<Speak language="hi-IN">Maaf kijiye' in sorry and "<Hangup/>" in sorry
    assert "Speak" not in vobiz.after_stream_xml(apologise=False, language="english")
    assert "<Hangup/>" in vobiz.unavailable_xml("english")
    assert vobiz.valid_public_url("https://rider.example.in")
    assert not vobiz.valid_public_url("http://rider.example.in")
    assert not vobiz.valid_public_url("https://u:p@rider.example.in")


# ---- audio ------------------------------------------------------------------


def test_chunks_are_fixed_frames_and_flush_keeps_whole_samples() -> None:
    chunker = OutboundChunker(320, 1)
    chunks = chunker.push(b"\x55" * 1000)
    assert [len(c) for c in chunks] == [320, 320, 320] and chunker.pending == 40
    assert [len(c) for c in chunker.flush()] == [40]
    l16 = OutboundChunker(1920, 2)
    l16.push(b"\x01" * 101)
    assert [len(c) for c in l16.flush()] == [100]
    l16.push(b"\x01" * 10)
    l16.drop()
    assert l16.flush() == []
    with pytest.raises(ValueError):
        OutboundChunker(3, 2)


def test_resampling_keeps_duration_and_is_chunk_independent() -> None:
    tone = [int(8000 * math.sin(2 * math.pi * 440 * n / 16000)) for n in range(1600)]
    whole = Resampler(16000, 24000).process(_pcm(tone))
    pieces = Resampler(16000, 24000)
    data = _pcm(tone)
    split = b"".join(pieces.process(data[i : i + 333]) for i in range(0, len(data), 333))
    assert split == whole
    assert abs(len(_samples(whole)) - 2400) <= 2
    assert Resampler(16000, 16000).process(b"\x01\x00\x02") == b"\x01\x00"


def test_upsampled_values_interpolate_between_neighbours() -> None:
    out = _samples(Resampler(8000, 24000).process(_pcm([0, 300, 600])))
    assert out[:6] == [0, 100, 200, 300, 400, 500]


# ---- safety ---------------------------------------------------------------


def test_otp_like_codes_are_hidden_but_flat_numbers_stay() -> None:
    assert "4821" not in redact_secrets("Delivery OTP: 4821, flat B-402")
    assert "B-402" in redact_secrets("Delivery OTP: 4821, flat B-402")
    assert "9931" not in redact_secrets("otp hai 9931")
    assert "123456" not in redact_secrets("Your PIN is 123456")
    assert redact_secrets("Flat 1204, Tower C, gate 2") == "Flat 1204, Tower C, gate 2"
    assert "5521" not in redact_store_text("Out for delivery, share code 5521 with rider")
    assert "5521" not in redact_store_text("Out for delivery 5521")


def test_rider_requests_are_flagged() -> None:
    assert request_flags("bhaiya OTP bata do") == {"otp"}
    assert "money" in request_flags("20 rupaye extra charge lagega")
    assert "money" in request_flags("online pay kar do UPI se")
    assert "order_change" in request_flags("order cancel kar do")
    assert request_flags("gate pe hoon, flat number?") == set()


# ---- notes ----------------------------------------------------------------


def test_delivery_notes_are_cleaned_and_capped() -> None:
    notes = DeliveryNotes(landmark="  Near   Shiv mandir ", directions="Gate 2, Tower B, flat 402")
    assert notes.landmark == "Near Shiv mandir"
    assert DeliveryNotes(landmark="   ").landmark is None
    with pytest.raises(ValidationError):
        DeliveryNotes(directions="x" * 301)
    with pytest.raises(ValidationError):
        DeliveryNotes(handoff="leave_anywhere")


@pytest.mark.parametrize("text", ["OTP is 1234", "my UPI pin 4455", "password: abc", "see https://maps.example"])
def test_delivery_notes_refuse_secrets_and_links(text: str) -> None:
    with pytest.raises(ValidationError):
        DeliveryNotes(directions=text)


def test_outcome_text_matches_the_app_card() -> None:
    from datetime import UTC, datetime

    def summary(**kw):
        return RiderCallSummary(started_at=datetime.now(UTC), caller="x", summary="s", **{"outcome": "answered", **kw})

    assert outcome_text(summary(answered_from=["notes"])) == "answered from your notes"
    assert outcome_text(summary(answered_from=["notes", "order"])) == "answered from your notes and order"
    assert outcome_text(summary(outcome="assistant_unavailable")) == "the assistant couldn't answer"
    assert outcome_text(summary(outcome="in_progress")) == "the rider is on the line"


def test_settings_update_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        RiderSettingsUpdate.model_validate({"enabled": True, "owner": "x"})


# ---- prompt ---------------------------------------------------------------


def test_greeting_identifies_as_ai_in_the_chosen_language() -> None:
    assert greeting("Moin", "hinglish") == "Namaste, main Moin ki AI assistant hoon — kya aap delivery ke liye call kar rahe hain?"
    assert greeting("Moin", "english").startswith("Hello, I'm Moin's AI assistant")
    assert "AI" in greeting(None, "hinglish")
    instructions = rider_instructions("Moin", "hinglish")
    for rule in ("never Moin", "Never share any OTP", "Never agree to any extra charge", "Never change, cancel", "never guess or invent directions", "Payment aap darwaze par Moin se le lijiye"):
        assert rule in instructions
