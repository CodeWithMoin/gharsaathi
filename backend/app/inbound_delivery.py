"""Server-side registration of one approved inbound delivery caller."""

import hashlib
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from uuid import uuid4

import httpx
from fastapi import Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import Settings, get_settings


_E164 = re.compile(r"^\+[1-9]\d{7,14}$")
_SARVAM_SCHEDULING_URL = "https://apps.sarvam.ai/api/scheduling"


class DeliveryContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipient_name: str = Field(min_length=2, max_length=80)
    delivery_address: str = Field(min_length=8, max_length=350)
    landmark: str = Field(min_length=3, max_length=180)
    confirmed: Literal[True]

    @field_validator("recipient_name", "delivery_address", "landmark")
    @classmethod
    def clean_text(cls, value: str) -> str:
        if any(ord(character) < 32 and character not in "\t\n\r" for character in value):
            raise ValueError("Provide plain text delivery details.")
        return " ".join(value.split())


class InboundDeliveryConfig(BaseModel):
    enabled: bool
    inbound_phone_number: str | None
    approved_caller_hint: str | None


class DeliveryContextStatus(BaseModel):
    cohort_id: str
    status: Literal["processing", "completed", "failed"]
    inbound_phone_number: str
    valid_records: int | None = None
    rejected_records: int | None = None


class InboundDeliveryService:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.client = client
        self._cohort_owners: dict[str, str] = {}

    @staticmethod
    def _user_key(user_id: str) -> str:
        return hashlib.sha256(user_id.encode()).hexdigest()[:24]

    def config(self) -> InboundDeliveryConfig:
        caller = self.settings.sarvam_demo_recipient_number.strip()
        inbound = self.settings.sarvam_inbound_phone_number.strip()
        ready = (
            bool(self.settings.sarvam_voice_agent_api_key.get_secret_value().strip())
            and bool(self.settings.sarvam_voice_agent_org_id)
            and bool(self.settings.sarvam_voice_agent_workspace_id)
            and bool(self.settings.sarvam_inbound_deployment_id)
            and bool(_E164.fullmatch(caller))
            and bool(_E164.fullmatch(inbound))
        )
        hint = f"•••• {caller[-4:]}" if ready else None
        return InboundDeliveryConfig(
            enabled=ready,
            inbound_phone_number=inbound if ready else None,
            approved_caller_hint=hint,
        )

    @asynccontextmanager
    async def _http_client(self) -> AsyncIterator[httpx.AsyncClient]:
        if self.client is not None:
            yield self.client
        else:
            async with httpx.AsyncClient(timeout=12.0) as client:
                yield client

    def _cohort_url(self, cohort_id: str | None = None) -> str:
        settings = self.settings
        base = (
            f"{_SARVAM_SCHEDULING_URL}/v1/orgs/{settings.sarvam_voice_agent_org_id}"
            f"/workspaces/{settings.sarvam_voice_agent_workspace_id}"
            f"/deployments/{settings.sarvam_inbound_deployment_id}/cohorts"
        )
        return f"{base}/{cohort_id}" if cohort_id else f"{base}/stream"

    def _headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self.settings.sarvam_voice_agent_api_key.get_secret_value(),
            "Content-Type": "application/json",
        }

    def _parse_status(self, payload: object) -> DeliveryContextStatus:
        if not isinstance(payload, dict):
            raise ValueError("invalid response")
        cohort_id = payload.get("cohort_id")
        cohort_status = payload.get("status")
        if not isinstance(cohort_id, str) or cohort_status not in {"processing", "completed", "failed"}:
            raise ValueError("invalid response")
        result = payload.get("result")
        result = result if isinstance(result, dict) else {}
        return DeliveryContextStatus(
            cohort_id=cohort_id,
            status=cohort_status,
            inbound_phone_number=self.settings.sarvam_inbound_phone_number,
            valid_records=result.get("valid_records") if isinstance(result.get("valid_records"), int) else None,
            rejected_records=result.get("rejected_records") if isinstance(result.get("rejected_records"), int) else None,
        )

    async def prepare(
        self, user_id: str, context: DeliveryContextRequest
    ) -> DeliveryContextStatus:
        if not self.config().enabled:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Inbound delivery guidance is not configured.",
            )
        payload = {
            "name": f"delivery-context-{uuid4().hex[:12]}",
            "users": [{
                "user_phone_number": self.settings.sarvam_demo_recipient_number,
                "user_identifier": self._user_key(user_id),
                "app_variables": {
                    "caller_authorized": "yes",
                    "recipient_name": context.recipient_name,
                    "delivery_address": context.delivery_address,
                    "landmark": context.landmark,
                },
            }],
        }
        try:
            async with self._http_client() as client:
                response = await client.post(
                    self._cohort_url(), headers=self._headers(), json=payload
                )
        except httpx.RequestError as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "Delivery context outcome is unknown. Check Sarvam before trying again.",
            ) from exc
        if response.status_code != 200:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "Sarvam did not accept the delivery context.",
            )
        try:
            parsed = self._parse_status(response.json())
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "Sarvam returned an invalid delivery-context response.",
            ) from exc
        self._cohort_owners[parsed.cohort_id] = self._user_key(user_id)
        return parsed

    async def status(self, user_id: str, cohort_id: str) -> DeliveryContextStatus:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", cohort_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Delivery context not found.")
        if self._cohort_owners.get(cohort_id) != self._user_key(user_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Delivery context not found.")
        try:
            async with self._http_client() as client:
                response = await client.get(
                    self._cohort_url(cohort_id), headers=self._headers()
                )
        except httpx.RequestError as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "Could not check the delivery context.",
            ) from exc
        if response.status_code != 200:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "Could not check the delivery context.",
            )
        try:
            return self._parse_status(response.json())
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "Sarvam returned an invalid delivery-context response.",
            ) from exc


_service: InboundDeliveryService | None = None


def get_inbound_delivery_service() -> InboundDeliveryService:
    global _service
    if _service is None:
        _service = InboundDeliveryService(get_settings())
    return _service


InboundDeliveryServiceDependency = Annotated[
    InboundDeliveryService, Depends(get_inbound_delivery_service)
]
