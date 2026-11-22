"""Inbound delivery-line call outcomes: parsing, redaction, attribution and copy. Pure logic."""

from datetime import UTC, datetime, timedelta

import pytest

from app.inbound_calls import (
    CALL_OUTCOMES,
    SUMMARY_MAX,
    ParsedCall,
    Watch,
    attribute,
    headline,
    mask_caller,
    outcome_text,
    parse_interaction,
    redact_summary,
)
from app.notifications import messages

DEPLOYMENT = "Moin-AI-Inb-711358d1-fb26"
AT = datetime(2026, 11, 22, 8, 44, tzinfo=UTC)  # 2:14 pm India time


def item(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "interaction_id": "20261122/9f1c2a7e-4b3d-4e8a-a1f0-6c2d9b7e5a14",
        "start_datetime": "2026-11-22T08:44:00",
        "end_datetime": "2026-11-22T08:45:10",
        "duration_in_seconds": 70.2,
        "user_contact": "+919876542590",
        "user_contact_masked": "+91******2590",
        "channel_direction": "inbound",
        "deployment_id": DEPLOYMENT,
        "app_version": 2,
        "is_debug_call": 0,
        "agent_variables": {
            "call_outcome": "route_confirmed",
            "delivery_guidance_summary": "Rider was guided to the gate opposite the park.",
            "delivery_provider": "zepto",
            "caller_authorized": "yes",
            "delivery_address": "12 Test Street, HSR Layout, Bengaluru 560102",
        },
    }
    values.update(overrides)
    return values


def test_parses_an_inbound_call_without_keeping_the_full_number_or_address() -> None:
    call = parse_interaction(item(), DEPLOYMENT)
    assert call is not None
    assert call.started_at == AT and call.ended_at == AT + timedelta(seconds=70)
    assert (call.provider, call.outcome, call.caller) == ("zepto", "route_confirmed", "•••• 2590")
    assert call.summary == "Rider was guided to the gate opposite the park."
    text = repr(call)
    assert "9876542590" not in text and "Test Street" not in text and "560102" not in text


@pytest.mark.parametrize(
    "overrides",
    [
        {"channel_direction": "outbound"},
        {"deployment_id": "another-deployment"},
        {"deployment_id": None},
        {"is_debug_call": 1},
        {"interaction_id": "no-slash"},
        {"interaction_id": None},
        {"start_datetime": "yesterday"},
        {"start_datetime": None},
    ],
)
def test_ignores_calls_that_are_not_inbound_calls_on_this_deployment(overrides: dict[str, object]) -> None:
    assert parse_interaction(item(**overrides), DEPLOYMENT) is None


def test_ignores_items_that_are_not_objects() -> None:
    assert parse_interaction("20261122/x", DEPLOYMENT) is None
    assert parse_interaction(None, DEPLOYMENT) is None


def test_unknown_outcome_and_provider_are_not_trusted() -> None:
    call = parse_interaction(
        item(agent_variables={"call_outcome": "refund_issued", "delivery_provider": "Amazon"}), DEPLOYMENT
    )
    assert call is not None and call.outcome is None and call.provider is None and call.summary is None
    missing = parse_interaction(item(agent_variables="not-a-dict"), DEPLOYMENT)
    assert missing is not None and missing.outcome is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Rider confirmed the route and is two minutes away.", "Rider confirmed the route and is two minutes away."),
        ("Caller asked for OTP 4821; agent refused.", "Caller asked for OTP [hidden]; agent refused."),
        ("Rider read out code 482193 and asked again", "Rider read out code and asked again"),
        ("Call back on 98765 43210 if needed", "Call back on if needed"),
        ("Order 1234567890 needs help from Zepto", "Order needs help from Zepto"),
        ("Rider needs the 4-digit pin 7351 now", "Rider needs the 4-digit pin [hidden] now"),
    ],
)
def test_summary_loses_codes_and_long_numbers(raw: str, expected: str) -> None:
    assert redact_summary(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "Shared 12 Test Street, HSR Layout with the rider",
        "Guided to Flat B-402, Lake View Apartments",
        "Told the rider: tower 3, near main gate",
        "Address is Sector 4 road behind the mall",
    ],
)
def test_summary_that_reads_like_an_address_is_dropped(raw: str) -> None:
    assert redact_summary(raw) is None


def test_summary_is_one_line_and_at_most_140_characters() -> None:
    long = "Rider\nwas guided " + "very carefully " * 20
    summary = redact_summary(long)
    assert summary is not None and len(summary) <= SUMMARY_MAX and "\n" not in summary
    assert summary.endswith("…")
    assert redact_summary("") is None and redact_summary("   ") is None and redact_summary(42) is None
    assert redact_summary("OTP 1234") == "OTP [hidden]"


def test_caller_keeps_the_last_four_digits_only() -> None:
    assert mask_caller("+91******2590") == "•••• 2590"
    assert mask_caller("+919876542590") == "•••• 2590"
    assert mask_caller("***") is None and mask_caller(None) is None


@pytest.mark.parametrize(
    ("outcome", "provider", "title", "text"),
    [
        ("route_confirmed", "zepto", "Zepto rider called", "Route confirmed"),
        ("owner_transfer_requested", "zepto", "Zepto rider called", "Caller asked for you"),
        ("provider_contact_needed", "zepto", "Zepto rider called", "Rider needed help — check the Zepto app"),
        ("provider_contact_needed", "swiggy", "Swiggy rider called", "Rider needed help — check the Swiggy app"),
        ("unauthorized_caller", None, "Someone called the delivery line", "Unverified caller — no address shared"),
        ("general_request_handled", "zepto", "Zepto rider called", "Question answered"),
        ("out_of_scope", "zepto", "Someone called the delivery line", "Not about a delivery"),
        ("no_decision", None, "A delivery partner called", "No outcome recorded"),
    ],
)
def test_each_outcome_has_its_own_copy(outcome: str, provider: str | None, title: str, text: str) -> None:
    assert outcome in CALL_OUTCOMES
    assert headline(provider, outcome) == title
    assert outcome_text(outcome, provider) == text


def test_every_outcome_is_covered_by_the_copy() -> None:
    texts = {outcome_text(outcome, "zepto") for outcome in CALL_OUTCOMES}
    assert len(texts) == len(CALL_OUTCOMES)


def test_push_is_private_time_and_outcome_only() -> None:
    payload = messages.inbound_rider_called(
        AT, headline("zepto", "route_confirmed"), outcome_text("route_confirmed", "zepto"), call_id="abc-1"
    )
    assert payload.kind == "rider_called"
    assert payload.title == "Zepto rider called"
    assert payload.body == "2:14 pm — Route confirmed."
    assert payload.url == "/?inbound_call=abc-1" and payload.tag == "inbound:abc-1"


def call(**overrides: object) -> ParsedCall:
    values: dict[str, object] = dict(
        interaction_id="20261122/a",
        started_at=AT,
        ended_at=AT,
        provider="zepto",
        outcome="route_confirmed",
        summary=None,
        caller=None,
        user_identifier=None,
        cohort_id=None,
    )
    values.update(overrides)
    return ParsedCall(**values)  # type: ignore[arg-type]


def test_attribution_prefers_the_users_own_cohort_or_identifier() -> None:
    early = AT - timedelta(minutes=30)
    watches = [Watch("u1", early, "key-1", {"cohort-1"}), Watch("u2", early, "key-2", {"cohort-2"})]
    assert attribute(call(cohort_id="cohort-2"), watches) == "u2"
    assert attribute(call(user_identifier="key-1"), watches) == "u1"
    # Two users waiting and nothing identifies the caller: shown to nobody.
    assert attribute(call(), watches) is None
    # One user waiting: an unknown caller's call is theirs.
    assert attribute(call(), watches[:1]) == "u1"


def test_attribution_never_crosses_users_or_time_windows() -> None:
    later = AT + timedelta(minutes=5)
    watches = [Watch("u1", AT - timedelta(minutes=5), "key-1", set()), Watch("u2", later, "key-2", {"cohort-2"})]
    # u2's cohort, but the call came before u2 started waiting: not u1's either.
    assert attribute(call(cohort_id="cohort-2"), watches) is None
    # A cohort GharSaathi doesn't know is never attributed.
    assert attribute(call(cohort_id="cohort-x"), watches) is None
    # Known owner from the database, outside the 2 h window: not shown.
    assert attribute(call(cohort_id="old"), watches, {"old": "u3"}) is None
    assert attribute(call(cohort_id="old"), watches, {"old": "u1"}) == "u1"
    assert attribute(call(user_identifier="key-2"), watches) is None
