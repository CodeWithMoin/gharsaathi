import json
import logging

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.inbound_delivery.models import (
    DeliveryDirections,
    LineSettingsUpdate,
    PrepareRequest,
)
from app.inbound_delivery.sarvam import (
    NOT_AVAILABLE,
    CohortResult,
    SarvamCohorts,
    SarvamError,
    clear_variables,
    mask_number,
    parse_cohort,
    share_variables,
)
from app.inbound_delivery.service import (
    MAX_TRACKED_COHORTS,
    InboundDeliveryService,
)

CALLER = "+919876543210"
ADDRESS = "Flat 402, Lake View Apartments, 12 Test Street, HSR Layout, Bengaluru"


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "commerce_mode": "demo",
        "sarvam_voice_agent_api_key": "voice-agent-key",
        "sarvam_voice_agent_org_id": "org-1",
        "sarvam_voice_agent_workspace_id": "workspace-1",
        "sarvam_inbound_deployment_id": "deployment-1",
        "sarvam_inbound_phone_number": "+919999999999",
        "sarvam_demo_recipient_number": CALLER,
        "sarvam_inbound_owner_user_id": "owner-1",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def share(**overrides: object) -> dict[str, str]:
    values: dict[str, object] = {
        "recipient_name": "Moin",
        "delivery_address": ADDRESS,
        "landmark": "Opposite the public park",
        "route_directions": ["Main road pe Reliance Smart ke baad left", "Doosri gali", "Blue gate"],
        "gate_note": None,
        "handover_instructions": "Guard ke paas chhod dijiye",
        "include_route": False,
    }
    values.update(overrides)
    return share_variables(**values)  # type: ignore[arg-type]


def cohort(status: str = "completed", valid: int = 1, rejected: int = 0) -> dict[str, object]:
    return {
        "name": "delivery-share-test",
        "cohort_id": "cohort-1",
        "status": status,
        "source_type": "file_upload",
        "cohort_category": "cohort",
        "created_by": "test",
        "created_at": "2026-11-22T00:00:00Z",
        "updated_at": "2026-11-22T00:00:00Z",
        "result": {"total_records": valid + rejected, "valid_records": valid, "rejected_records": rejected}
        if status == "completed" else None,
    }


# Variables ---------------------------------------------------------------------


def test_agent_v5_gets_only_its_four_declared_variables() -> None:
    variables = share()
    assert variables == {
        "caller_authorized": "yes",
        "recipient_name": "Moin",
        "delivery_address": ADDRESS,
        "landmark": "Opposite the public park",
    }
    assert all(isinstance(value, str) for value in variables.values())


def test_route_and_handover_are_sent_only_with_the_flag() -> None:
    variables = share(include_route=True, gate_note="Ring the bell twice")
    assert variables["route_directions"] == (
        "1) Main road pe Reliance Smart ke baad left; 2) Doosri gali; 3) Blue gate; 4) Gate: Ring the bell twice"
    )
    assert variables["handover_instructions"] == "Guard ke paas chhod dijiye"
    assert variables["landmark"] == "Opposite the public park"
    assert set(variables) == {
        "caller_authorized", "recipient_name", "delivery_address", "landmark",
        "route_directions", "handover_instructions",
    }


def test_unsaved_route_fields_use_the_agent_default_when_the_flag_is_on() -> None:
    variables = share(include_route=True, route_directions=[], handover_instructions=None)
    assert variables["route_directions"] == NOT_AVAILABLE
    assert variables["handover_instructions"] == NOT_AVAILABLE


def test_gate_note_rides_with_the_landmark_for_agent_v5() -> None:
    variables = share(gate_note="Blue gate, ring twice")
    assert variables["landmark"] == "Opposite the public park. Gate: Blue gate, ring twice"
    assert "route_directions" not in variables


@pytest.mark.parametrize("address", ["", "   "])
def test_never_authorises_a_caller_without_an_address(address: str) -> None:
    with pytest.raises(ValueError):
        share(delivery_address=address)


@pytest.mark.parametrize("include_route", [False, True])
def test_clear_record_withdraws_authorisation_and_blanks_every_detail(include_route: bool) -> None:
    variables = clear_variables(include_route=include_route)
    assert variables.pop("caller_authorized") == "no"
    assert set(variables.values()) == {""}
    expected = {"recipient_name", "delivery_address", "landmark"}
    if include_route:
        expected |= {"route_directions", "handover_instructions"}
    assert set(variables) == expected


# Approval and directions ---------------------------------------------------------


@pytest.mark.parametrize("confirmed", [1, "true", "yes", 0, False, None])
def test_only_json_true_approves_sharing(confirmed: object) -> None:
    with pytest.raises(ValidationError):
        PrepareRequest.model_validate({"provider": "zepto", "address_id": "a1", "confirmed": confirmed})
    assert PrepareRequest.model_validate({"provider": "zepto", "address_id": "a1", "confirmed": True}).confirmed is True


def test_browser_cannot_send_its_own_address_or_number() -> None:
    with pytest.raises(ValidationError):
        PrepareRequest.model_validate({
            "provider": "zepto", "address_id": "a1", "confirmed": True, "delivery_address": "Elsewhere",
        })
    with pytest.raises(ValidationError):
        PrepareRequest.model_validate({
            "provider": "zepto", "address_id": "a1", "confirmed": True, "user_phone_number": "+911234567890",
        })


def test_turning_on_auto_share_needs_its_own_strict_approval() -> None:
    with pytest.raises(ValidationError):
        LineSettingsUpdate.model_validate({"auto_share": True})
    with pytest.raises(ValidationError):
        LineSettingsUpdate.model_validate({"auto_share": True, "confirmed": 1})
    with pytest.raises(ValidationError):
        LineSettingsUpdate.model_validate({"auto_share": 1, "confirmed": True})
    assert LineSettingsUpdate.model_validate({"auto_share": False}).auto_share is False


def test_directions_are_cleaned_and_capped() -> None:
    directions = DeliveryDirections.model_validate({
        "landmark": "  Opposite   the park ",
        "route_directions": ["Main road", "  ", "Lift se 4th floor, flat 402"],
        "gate_note": "",
        "handover_instructions": " Door ke bahar rakh dijiye ",
    })
    assert directions.landmark == "Opposite the park"
    assert directions.route_directions == ["Main road", "Lift se 4th floor, flat 402"]
    assert directions.gate_note is None
    assert directions.handover_instructions == "Door ke bahar rakh dijiye"
    with pytest.raises(ValidationError):
        DeliveryDirections.model_validate({"landmark": "Park", "route_directions": ["step"] * 9})
    with pytest.raises(ValidationError):
        DeliveryDirections.model_validate({"landmark": "Park", "route_directions": ["x" * 81]})
    with pytest.raises(ValidationError):
        DeliveryDirections.model_validate({"landmark": "Park", "handover_instructions": "x" * 101})


@pytest.mark.parametrize(
    "field,value",
    [
        ("landmark", "Gate OTP is 4821"),
        ("handover_instructions", "Pay by UPI at the door"),
        ("gate_note", "See https://maps.example.test"),
        ("landmark", "Park\nsecond line"),
    ],
)
def test_directions_refuse_secrets_links_and_extra_lines(field: str, value: str) -> None:
    body = {"landmark": "Opposite the park", field: value}
    with pytest.raises(ValidationError):
        DeliveryDirections.model_validate(body)


def test_numbers_are_masked_to_the_last_four_digits() -> None:
    assert mask_number(CALLER) == "•••• 3210"
    assert mask_number("12") == "••••"


# Cohort results ------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload,ready",
    [
        (cohort(), True),
        (cohort(valid=1, rejected=1), False),
        (cohort(valid=0, rejected=1), False),
        (cohort(valid=2, rejected=0), False),
        (cohort("processing"), False),
        (cohort("failed"), False),
        ({**cohort(), "result": None}, False),
        ({**cohort(), "result": {"valid_records": True, "rejected_records": 0}}, False),
    ],
)
def test_ready_only_for_exactly_one_valid_and_zero_rejected(payload: dict, ready: bool) -> None:
    assert parse_cohort(payload).accepted_one is ready


@pytest.mark.parametrize("payload", [[], {"status": "completed"}, {"cohort_id": "x", "status": "done"}, {"cohort_id": "../x", "status": "completed"}])
def test_unreadable_cohort_payloads_are_refused(payload: object) -> None:
    with pytest.raises(ValueError):
        parse_cohort(payload)


# Sarvam HTTP mapping --------------------------------------------------------------


async def stream_with(handler) -> CohortResult:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await SarvamCohorts(settings(), client).stream(
            user_identifier="hashed", variables=share(), kind="share"
        )


@pytest.mark.asyncio
async def test_streams_one_record_for_the_server_configured_caller() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["key"] = request.headers["X-API-Key"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=cohort())

    result = await stream_with(handler)
    assert result.accepted_one
    assert captured["url"] == (
        "https://apps.sarvam.ai/api/scheduling/v1/orgs/org-1/workspaces/"
        "workspace-1/deployments/deployment-1/cohorts/stream"
    )
    assert captured["key"] == "voice-agent-key"
    body = captured["body"]
    assert isinstance(body, dict) and len(body["users"]) == 1
    assert 1 <= len(body["name"]) <= 50
    assert body["users"][0]["user_phone_number"] == CALLER


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response,status_code,code",
    [
        (httpx.Response(401), 503, "sarvam_not_authorised"),
        (httpx.Response(403), 503, "sarvam_not_authorised"),
        (httpx.Response(422, json={"error": {"code": "bad", "message": "number +919876543210 bad"}}), 502, "sarvam_rejected"),
        (httpx.Response(404), 502, "sarvam_rejected"),
        (httpx.Response(429), 503, "sarvam_unavailable"),
        (httpx.Response(500), 503, "sarvam_unavailable"),
        (httpx.Response(503), 503, "sarvam_unavailable"),
        (httpx.Response(200, text="<html>"), 502, "sarvam_unreadable"),
        (httpx.Response(200, json={"cohort_id": "c"}), 502, "sarvam_unreadable"),
    ],
)
async def test_sarvam_responses_map_to_clear_outcomes(
    response: httpx.Response, status_code: int, code: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    with pytest.raises(SarvamError) as error:
        await stream_with(lambda _: response)
    assert (error.value.status_code, error.value.code) == (status_code, code)
    logged = caplog.text
    assert "3210" not in logged and "Lake View" not in logged and "voice-agent-key" not in logged


@pytest.mark.asyncio
async def test_connection_failure_is_unavailable_and_timeout_is_unknown_without_retry() -> None:
    calls = 0

    def refused(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("refused")

    with pytest.raises(SarvamError) as error:
        await stream_with(refused)
    assert (error.value.status_code, error.value.code, calls) == (503, "sarvam_unavailable", 1)

    calls = 0

    def slow(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timed out")

    with pytest.raises(SarvamError) as error:
        await stream_with(slow)
    assert (error.value.status_code, error.value.code, calls) == (504, "outcome_unknown", 1)


# Config and guard ---------------------------------------------------------------


class Unused:
    def __getattr__(self, name: str):
        raise AssertionError(f"not expected: {name}")


def service(**overrides: object) -> InboundDeliveryService:
    return InboundDeliveryService(settings(**overrides), Unused(), Unused(), Unused())  # type: ignore[arg-type]


def test_config_masks_the_caller_and_names_the_owner_guard() -> None:
    owner = service().config("owner-1")
    assert owner.enabled is True and owner.approved_caller_hint == "•••• 3210"
    # Agent v7 declares route_directions and handover_instructions: on by default.
    assert owner.route_directions_enabled is True
    assert Settings(_env_file=None).sarvam_route_directions_enabled is True
    other = service().config("someone-else")
    assert (other.enabled, other.reason, other.inbound_phone_number, other.approved_caller_hint) == (
        False, "not_owner", None, None,
    )


def test_allow_list_is_the_guard_when_no_owner_is_set_and_neither_means_off() -> None:
    allow_listed = service(sarvam_inbound_owner_user_id="", allowed_user_ids="owner-1")
    assert allow_listed.config("owner-1").enabled is True
    assert allow_listed.config("intruder").reason == "not_owner"
    open_server = service(sarvam_inbound_owner_user_id="")
    assert open_server.config("anyone").reason == "owner_not_configured"


def test_config_is_off_without_complete_setup_and_ignores_commerce_mode() -> None:
    assert service(sarvam_demo_recipient_number="98765").config("owner-1").reason == "not_configured"
    assert service(sarvam_voice_agent_api_key="").config("owner-1").reason == "not_configured"
    assert service(commerce_mode="live").config("owner-1").enabled is True
    assert service(sarvam_route_directions_enabled=False).config("owner-1").route_directions_enabled is False


def test_cohort_owner_map_is_capped() -> None:
    line = service()
    for index in range(MAX_TRACKED_COHORTS + 20):
        line._remember_cohort(f"cohort-{index}", "owner-1")
    assert len(line._cohort_owners) == MAX_TRACKED_COHORTS
    assert "cohort-0" not in line._cohort_owners
    assert f"cohort-{MAX_TRACKED_COHORTS + 19}" in line._cohort_owners
