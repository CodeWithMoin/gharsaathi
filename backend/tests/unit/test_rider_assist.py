"""Rider assist pure logic: Exotel protocol, audio chunking/resampling, auth, safety, notes."""

import array
import base64
import json
import math

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.rider.audio import (
    MAX_CHUNK_BYTES,
    MIN_CHUNK_BYTES,
    OutboundChunker,
    Resampler,
    chunk_bytes_for,
    valid_chunk,
)
from app.rider.auth import basic_credentials, stream_request_authorised
from app.rider.prompts import greeting, rider_instructions
from app.rider.protocol import (
    ProtocolError,
    clear_message,
    mark_message,
    mask_number,
    media_message,
    parse_event,
    resolve_sample_rate,
    same_number,
    sample_rate_from_query,
)
from app.rider.safety import redact_secrets, redact_store_text, request_flags
from app.rider.store import DeliveryNotes, RiderSettingsUpdate


def _pcm(samples: list[int]) -> bytes:
    return array.array("h", samples).tobytes()


def _samples(data: bytes) -> list[int]:
    values = array.array("h")
    values.frombytes(data)
    return list(values)


# ---- protocol -------------------------------------------------------------


def test_start_event_is_parsed_with_string_or_number_fields() -> None:
    event = parse_event(
        json.dumps(
            {
                "event": "start",
                "sequence_number": 1,
                "stream_sid": "MZ1",
                "start": {
                    "stream_sid": "MZ1",
                    "call_sid": "CA1",
                    "from": "+919876543210",
                    "to": "+918047491899",
                    "custom_parameters": {"k": "v"},
                    "media_format": {"encoding": "raw", "sample_rate": "16000", "bit_rate": "128"},
                },
            }
        )
    )
    assert event.kind == "start"
    assert event.start is not None
    assert event.start.stream_sid == "MZ1"
    assert event.start.sample_rate == 16000
    assert event.start.custom_parameters == {"k": "v"}


def test_media_dtmf_mark_stop_and_bad_messages() -> None:
    audio = _pcm([1, 2, 3])
    media = parse_event(json.dumps({"event": "media", "stream_sid": "MZ1", "media": {"chunk": 2, "timestamp": "10", "payload": base64.b64encode(audio).decode()}}))
    assert media.audio == audio
    assert parse_event('{"event":"dtmf","dtmf":{"digit":"5","duration":"100"}}').digit == "5"
    assert parse_event('{"event":"mark","mark":{"name":"end-call"}}').mark == "end-call"
    assert parse_event('{"event":"stop","stop":{"reason":"callended"}}').stop_reason == "callended"
    assert parse_event('{"event":"connected"}').kind == "connected"
    for bad in ("not json", "[]", '{"event":"media","media":{"payload":"***"}}', '{"event":"start"}', '{"event":"bogus"}'):
        with pytest.raises(ProtocolError):
            parse_event(bad)


def test_outgoing_messages_follow_the_documented_shape() -> None:
    chunk = b"\x00" * 3200
    message = json.loads(media_message("MZ1", chunk))
    assert message == {"event": "media", "stream_sid": "MZ1", "media": {"payload": base64.b64encode(chunk).decode()}}
    assert json.loads(mark_message("MZ1", "m1")) == {"event": "mark", "stream_sid": "MZ1", "mark": {"name": "m1"}}
    assert json.loads(clear_message("MZ1")) == {"event": "clear", "stream_sid": "MZ1"}
    with pytest.raises(ValueError):
        media_message("MZ1", b"\x00" * 3000)


def test_sample_rate_comes_from_the_url_then_media_format_then_default() -> None:
    assert sample_rate_from_query("sample-rate=16000&x=1") == 16000
    assert sample_rate_from_query("sample-rate=11025") is None
    start = parse_event(json.dumps({"event": "start", "start": {"stream_sid": "MZ", "media_format": {"sample_rate": "24000"}}})).start
    assert resolve_sample_rate(start, 16000) == 16000
    assert resolve_sample_rate(start, None) == 24000
    assert resolve_sample_rate(None, None) == 8000


def test_numbers_are_masked_and_compared_by_last_ten_digits() -> None:
    assert mask_number("+919876543210") == "•••• 210"
    assert mask_number("") == "unknown"
    assert same_number("+918047491899", "08047491899")
    assert not same_number("+918047491899", "+918047491800")


# ---- audio ----------------------------------------------------------------


@pytest.mark.parametrize("rate", [8000, 16000, 24000])
def test_chunks_are_multiples_of_320_within_exotel_limits(rate: int) -> None:
    chunker = OutboundChunker(rate)
    assert valid_chunk(chunk_bytes_for(rate))
    chunks = []
    for size in (1, 999, 5000, 12345, 7, 64000):
        chunks += chunker.push(b"\x01" * size)
    chunks += chunker.flush()
    assert chunks and all(valid_chunk(len(chunk)) for chunk in chunks)
    assert all(len(chunk) % 320 == 0 and MIN_CHUNK_BYTES <= len(chunk) <= MAX_CHUNK_BYTES for chunk in chunks)
    # A tiny tail is padded up to the 3.2 KB minimum.
    chunker.push(b"\x01" * 10)
    (tail,) = chunker.flush()
    assert len(tail) == MIN_CHUNK_BYTES and tail[:10] == b"\x01" * 10 and set(tail[10:]) == {0}
    chunker.push(b"\x01" * 100)
    chunker.drop()
    assert chunker.flush() == []


def test_resampling_keeps_duration_and_is_chunk_independent() -> None:
    tone = [int(8000 * math.sin(2 * math.pi * 440 * n / 16000)) for n in range(1600)]
    whole = Resampler(16000, 24000).process(_pcm(tone))
    pieces = Resampler(16000, 24000)
    data = _pcm(tone)
    split = b"".join(pieces.process(data[i : i + 333]) for i in range(0, len(data), 333))
    assert split == whole
    assert abs(len(_samples(whole)) - 2400) <= 2
    down = Resampler(24000, 8000).process(_pcm([0, 300, 600] * 800))
    assert abs(len(_samples(down)) - 800) <= 2
    assert Resampler(16000, 16000).process(b"\x01\x00\x02") == b"\x01\x00"


def test_upsampled_values_interpolate_between_neighbours() -> None:
    out = _samples(Resampler(8000, 24000).process(_pcm([0, 300, 600])))
    assert out[:7] == [0, 100, 200, 300, 400, 500, 600][: len(out[:7])]


# ---- auth -----------------------------------------------------------------


def _settings(**overrides) -> Settings:
    values = {"exotel_stream_user": "exotel", "exotel_stream_password": SecretStr("s3cret-password-123456")}
    values.update(overrides)
    return Settings(**values)


def _basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def test_stream_auth_accepts_only_the_exact_pair() -> None:
    settings = _settings()
    assert stream_request_authorised(settings, _basic("exotel", "s3cret-password-123456"))
    for header in (None, "", "Bearer x", "Basic !!!", _basic("exotel", "wrong"), _basic("other", "s3cret-password-123456"), "Basic " + base64.b64encode(b"nocolon").decode()):
        assert not stream_request_authorised(settings, header)
    assert basic_credentials(_basic("a", "b:c")) == ("a", "b:c")


def test_stream_auth_refuses_everything_when_unconfigured_or_weak() -> None:
    assert not stream_request_authorised(_settings(exotel_stream_user=""), _basic("", "s3cret-password-123456"))
    assert not stream_request_authorised(_settings(exotel_stream_password=SecretStr("short")), _basic("exotel", "short"))


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
