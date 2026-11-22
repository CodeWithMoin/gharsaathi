import json

import httpx
import pytest
from fastapi import HTTPException

from app.core.config import Settings
from app.inbound_delivery import (
    DeliveryContextRequest,
    InboundDeliveryService,
)


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "commerce_mode": "demo",
        "sarvam_voice_agent_api_key": "voice-agent-key",
        "sarvam_voice_agent_org_id": "org-1",
        "sarvam_voice_agent_workspace_id": "workspace-1",
        "sarvam_inbound_deployment_id": "deployment-1",
        "sarvam_inbound_phone_number": "+918064264240",
        "sarvam_demo_recipient_number": "+919876543210",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def approved_context() -> DeliveryContextRequest:
    return DeliveryContextRequest(
        recipient_name="Moin",
        delivery_address="12 Test Street, HSR Layout, Bengaluru",
        landmark="Opposite the public park",
        confirmed=True,
    )


def cohort_payload(status: str = "completed") -> dict[str, object]:
    return {
        "name": "delivery-context-test",
        "cohort_id": "cohort-1",
        "status": status,
        "source_type": "file_upload",
        "cohort_category": "cohort",
        "created_by": "test",
        "created_at": "2026-11-22T00:00:00Z",
        "updated_at": "2026-11-22T00:00:00Z",
        "result": {
            "total_records": 1,
            "valid_records": 1,
            "rejected_records": 0,
        } if status == "completed" else None,
    }


@pytest.mark.asyncio
async def test_streams_exact_context_for_server_configured_caller() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["key"] = request.headers["X-API-Key"]
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json=cohort_payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await InboundDeliveryService(settings(), client).prepare(
            "clerk-user-id", approved_context()
        )

    assert result.model_dump() == {
        "cohort_id": "cohort-1",
        "status": "completed",
        "inbound_phone_number": "+918064264240",
        "valid_records": 1,
        "rejected_records": 0,
    }
    assert captured["url"] == (
        "https://apps.sarvam.ai/api/scheduling/v1/orgs/org-1/workspaces/"
        "workspace-1/deployments/deployment-1/cohorts/stream"
    )
    assert captured["key"] == "voice-agent-key"
    body = captured["payload"]
    assert isinstance(body, dict)
    user = body["users"][0]
    assert user["user_phone_number"] == "+919876543210"
    assert user["user_identifier"] != "clerk-user-id"
    assert user["app_variables"] == {
        "caller_authorized": "yes",
        "recipient_name": "Moin",
        "delivery_address": "12 Test Street, HSR Layout, Bengaluru",
        "landmark": "Opposite the public park",
    }


@pytest.mark.asyncio
async def test_reads_documented_cohort_status_endpoint() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if request.method == "POST":
            return httpx.Response(200, json=cohort_payload("processing"))
        assert str(request.url).endswith("/deployments/deployment-1/cohorts/cohort-1")
        return httpx.Response(200, json=cohort_payload("completed"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = InboundDeliveryService(settings(), client)
        await service.prepare("user-1", approved_context())
        result = await service.status("user-1", "cohort-1")
    assert result.status == "completed"
    assert calls == 2


@pytest.mark.asyncio
async def test_cohort_status_is_scoped_to_preparing_user() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=cohort_payload("processing"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = InboundDeliveryService(settings(), client)
        await service.prepare("user-1", approved_context())
        with pytest.raises(HTTPException) as error:
            await service.status("user-2", "cohort-1")
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_network_error_is_unknown_and_is_not_retried() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timed out")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(HTTPException) as error:
            await InboundDeliveryService(settings(), client).prepare(
                "user-1", approved_context()
            )
    assert error.value.status_code == 502
    assert "unknown" in error.value.detail.lower()
    assert calls == 1


def test_config_masks_caller_and_works_independently_of_commerce_mode() -> None:
    config = InboundDeliveryService(settings()).config()
    assert config.enabled is True
    assert config.approved_caller_hint == "•••• 3210"
    assert InboundDeliveryService(settings(commerce_mode="live")).config().enabled is True


def test_context_requires_explicit_confirmation() -> None:
    with pytest.raises(ValueError):
        DeliveryContextRequest(
            recipient_name="Moin",
            delivery_address="12 Test Street",
            landmark="Public park",
            confirmed=False,
        )
