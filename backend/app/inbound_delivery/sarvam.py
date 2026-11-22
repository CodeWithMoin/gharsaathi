"""Sarvam inbound-deployment cohorts: the only Sarvam calls this app makes.

Docs: https://docs.sarvam.ai/voice-agents/api/deployments/cohorts/stream and
.../cohorts/get. A streamed cohort attaches ``app_variables`` to a known caller
number; there is no documented delete for one record. Clearing therefore streams
a replacement record for the same number with ``caller_authorized=no`` and empty
details. The docs describe streaming as a way to "add or refresh contacts" but do
not say which cohort wins when one number appears in several, so the newest
record winning is an assumption (specs/inbound-delivery-assist.md).

Logs carry the operation and HTTP status code only: never numbers, addresses,
directions, provider text or the API key.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)

SARVAM_SCHEDULING_URL = "https://apps.sarvam.ai/api/scheduling"
E164 = re.compile(r"^\+[1-9]\d{7,14}$")
COHORT_ID = re.compile(r"^[A-Za-z0-9_-]{1,200}$")

# Declared on the deployed agent (v5). Nothing else is ever sent.
BASE_VARIABLES = ("caller_authorized", "recipient_name", "delivery_address", "landmark")
# Declared only on the newer agent version; sent with SARVAM_ROUTE_DIRECTIONS_ENABLED=true.
ROUTE_VARIABLES = ("route_directions", "handover_instructions")
# The agent's default for an unset variable; used when the user saved nothing.
NOT_AVAILABLE = "NOT_AVAILABLE"

CohortStatus = Literal["processing", "completed", "failed"]


def mask_number(number: str) -> str:
    digits = re.sub(r"\D", "", number)
    return f"•••• {digits[-4:]}" if len(digits) >= 4 else "••••"


class SarvamError(Exception):
    """A Sarvam call that did not give a usable answer, mapped to what the user can do."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CohortResult:
    cohort_id: str
    status: CohortStatus
    valid_records: int | None
    rejected_records: int | None

    @property
    def accepted_one(self) -> bool:
        """Completed with exactly one valid record and zero rejected."""

        return (
            self.status == "completed"
            and self.valid_records == 1
            and self.rejected_records == 0
        )


def share_variables(
    *,
    recipient_name: str,
    delivery_address: str,
    landmark: str,
    route_directions: list[str],
    gate_note: str | None,
    handover_instructions: str | None,
    include_route: bool,
) -> dict[str, str]:
    """Variables that authorise the caller. Never ``yes`` without an address."""

    address = " ".join(delivery_address.split())
    if not address:
        raise ValueError("caller_authorized=yes needs a delivery address")
    variables = {
        "caller_authorized": "yes",
        "recipient_name": " ".join(recipient_name.split()),
        "delivery_address": address,
        "landmark": landmark,
    }
    steps = list(route_directions)
    if include_route:
        if gate_note:
            steps.append(f"Gate: {gate_note}")
        variables["route_directions"] = (
            "; ".join(f"{index}) {step}" for index, step in enumerate(steps, 1)) or NOT_AVAILABLE
        )
        variables["handover_instructions"] = handover_instructions or NOT_AVAILABLE
    elif gate_note:
        # Agent v5 has no route variable: the gate note rides with the landmark.
        variables["landmark"] = f"{landmark}. Gate: {gate_note}"
    return _checked(variables, include_route)


def clear_variables(*, include_route: bool) -> dict[str, str]:
    variables = {name: "" for name in BASE_VARIABLES}
    variables["caller_authorized"] = "no"
    if include_route:
        variables.update({name: "" for name in ROUTE_VARIABLES})
    return _checked(variables, include_route)


def _checked(variables: dict[str, str], include_route: bool) -> dict[str, str]:
    allowed = set(BASE_VARIABLES) | (set(ROUTE_VARIABLES) if include_route else set())
    if set(variables) - allowed or not all(isinstance(value, str) for value in variables.values()):
        raise ValueError("only declared string variables may be sent")
    if variables.get("caller_authorized") == "yes" and not variables.get("delivery_address"):
        raise ValueError("caller_authorized=yes needs a delivery address")
    return variables


class SarvamCohorts:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.client = client

    @asynccontextmanager
    async def _http(self) -> AsyncIterator[httpx.AsyncClient]:
        if self.client is not None:
            yield self.client
        else:
            async with httpx.AsyncClient(timeout=12.0) as client:
                yield client

    def _url(self, cohort_id: str | None = None) -> str:
        settings = self.settings
        base = (
            f"{SARVAM_SCHEDULING_URL}/v1/orgs/{settings.sarvam_voice_agent_org_id}"
            f"/workspaces/{settings.sarvam_voice_agent_workspace_id}"
            f"/deployments/{settings.sarvam_inbound_deployment_id}/cohorts"
        )
        return f"{base}/{cohort_id}" if cohort_id else f"{base}/stream"

    def _headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self.settings.sarvam_voice_agent_api_key.get_secret_value(),
            "Content-Type": "application/json",
        }

    async def stream(
        self, *, user_identifier: str, variables: dict[str, str], kind: Literal["share", "clear"]
    ) -> CohortResult:
        """One POST, never retried: a timeout leaves the outcome unknown."""

        payload = {
            "name": f"delivery-{kind}-{uuid4().hex[:12]}",
            "users": [{
                "user_phone_number": self.settings.sarvam_demo_recipient_number.strip(),
                "user_identifier": user_identifier,
                "app_variables": variables,
            }],
        }
        return await self._call("stream", "POST", self._url(), payload)

    async def get(self, cohort_id: str) -> CohortResult:
        if not COHORT_ID.fullmatch(cohort_id):
            raise SarvamError(404, "not_found", "Delivery line update not found.")
        return await self._call("get", "GET", self._url(cohort_id), None)

    async def _call(
        self, operation: str, method: str, url: str, payload: dict | None
    ) -> CohortResult:
        writing = method == "POST"
        try:
            async with self._http() as client:
                response = await client.request(
                    method, url, headers=self._headers(), json=payload
                )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            logger.warning("event=inbound_delivery_sarvam_error op=%s status=connect", operation)
            raise SarvamError(
                503, "sarvam_unavailable", "Sarvam can't be reached right now. Nothing was changed."
            ) from exc
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            # The request may have reached Sarvam. Never retried automatically.
            logger.warning("event=inbound_delivery_sarvam_error op=%s status=timeout", operation)
            if writing:
                raise SarvamError(
                    504,
                    "outcome_unknown",
                    "Sarvam didn't answer in time, so it isn't known whether the delivery line changed.",
                ) from exc
            raise SarvamError(
                504, "status_unknown", "Sarvam didn't answer in time. Check again in a moment."
            ) from exc
        code = response.status_code
        if code != 200:
            logger.warning("event=inbound_delivery_sarvam_error op=%s status=%s", operation, code)
        if code in {401, 403}:
            raise SarvamError(
                503,
                "sarvam_not_authorised",
                "Sarvam refused this server's Voice Agents key. Check the server setup.",
            )
        if code == 429 or code >= 500:
            raise SarvamError(
                503, "sarvam_unavailable", "Sarvam is busy or unavailable. Try again in a minute."
            )
        if code == 404 and not writing:
            raise SarvamError(404, "not_found", "Delivery line update not found.")
        if code != 200:
            raise SarvamError(
                502, "sarvam_rejected", "Sarvam did not accept the delivery line update."
            )
        try:
            return parse_cohort(response.json())
        except (ValueError, TypeError) as exc:
            logger.warning("event=inbound_delivery_sarvam_error op=%s status=unreadable", operation)
            raise SarvamError(
                502, "sarvam_unreadable", "Sarvam sent an answer that couldn't be read."
            ) from exc


def parse_cohort(payload: object) -> CohortResult:
    if not isinstance(payload, dict):
        raise ValueError("invalid response")
    cohort_id = payload.get("cohort_id")
    status = payload.get("status")
    if not isinstance(cohort_id, str) or not COHORT_ID.fullmatch(cohort_id):
        raise ValueError("invalid response")
    if status not in {"processing", "completed", "failed"}:
        raise ValueError("invalid response")
    result = payload.get("result")
    result = result if isinstance(result, dict) else {}

    def count(name: str) -> int | None:
        value = result.get(name)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    return CohortResult(
        cohort_id=cohort_id,
        status=status,
        valid_records=count("valid_records"),
        rejected_records=count("rejected_records"),
    )
