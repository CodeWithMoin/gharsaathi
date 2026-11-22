"""Outcomes of calls to the Sarvam inbound delivery line, shown in the app.

``RiderCallMonitor`` reads Sarvam's documented, read-only List interactions
endpoint (``GET https://apps.sarvam.ai/api/analytics/v1/{org_id}/{workspace_id}/{app_id}/interactions``,
https://docs.sarvam.ai/voice-agents/api/analytics/interactions) for the inbound
agent, at most once every 30 s, and only while some user has an active order or a
delivery context prepared in the last 2 h. Each new inbound call for the
configured deployment is attributed to exactly one user and stored as a small,
redacted summary (``InboundCallStore``); the user gets one ``rider_called``
browser notification for it.

What is kept: time, store, the agent's post-call ``call_outcome``, a one-line
summary (≤140 characters, codes and anything address-like removed) and the
caller's last four digits. Never audio, a transcript, the full number (the API
also returns ``user_contact`` unmasked; it is never read) or the address.
Logs carry counts and HTTP status codes only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, get_args

import httpx
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.database.models import InboundDeliveryContextRecord, InboundRiderCallRecord
from app.database.upsert import insert_if_absent
from app.notifications import messages
from app.rider.safety import HIDDEN, redact_store_text

logger = logging.getLogger(__name__)

ANALYTICS_URL = "https://apps.sarvam.ai/api/analytics/v1"

CallOutcome = Literal[
    "route_confirmed",
    "owner_transfer_requested",
    "provider_contact_needed",
    "unauthorized_caller",
    "general_request_handled",
    "out_of_scope",
    "no_decision",
]
CALL_OUTCOMES: tuple[str, ...] = get_args(CallOutcome)
Provider = Literal["zepto", "swiggy"]
STORE_NAMES: dict[str, str] = {"zepto": "Zepto", "swiggy": "Swiggy"}

POLL_SECONDS = 30.0
CONTEXT_WINDOW = timedelta(hours=2)
# An order still moving after this long no longer keeps the poll alive.
ORDER_WINDOW = timedelta(hours=6)
# Post-call analysis can land after the call row: wait this long before
# recording a call without an outcome as "no_decision".
OUTCOME_GRACE = timedelta(minutes=10)
MAX_BACKOFF_SECONDS = 15 * 60
AUTH_BACKOFF_SECONDS = 15 * 60
PAGE_LIMIT = 100
SUMMARY_MAX = 140
CALLS_LISTED = 20
SEEN_MEMORY = 1000

_INTERACTION_ID = re.compile(r"^\d{8}/[A-Za-z0-9_.:-]{1,140}$")
_LONG_DIGITS = re.compile(r"\+?\d(?:[\s().-]{0,2}\d){5,}")
_HIDDEN_RUN = re.compile(r"(?:\s*" + re.escape(HIDDEN) + r")+")
_ADDRESS_WORDS = re.compile(
    r"(?i)\b(?:flat|house|h\.?\s?no|plot|floor|tower|block|wing|apartments?|apts?|society|"
    r"sector|phase|road|rd|street|st|lane|marg|nagar|colony|layout|cross|main|pin\s?code|"
    r"building|bldg|villa|gali|mohalla|chowk)\b|फ्लैट|मकान|गली|सेक्टर"
)
_HAS_DIGIT = re.compile(r"\d")


def utc_now() -> datetime:
    return datetime.now(UTC)


# Copy ----------------------------------------------------------------------


def store_name(provider: str | None) -> str | None:
    return STORE_NAMES.get(provider or "")


def outcome_text(outcome: str, provider: str | None = None) -> str:
    """One short line per outcome; the frontend card uses the same wording."""

    store = store_name(provider)
    return {
        "route_confirmed": "Route confirmed",
        "owner_transfer_requested": "Caller asked for you",
        "provider_contact_needed": f"Rider needed help — check the {store or 'store'} app",
        "unauthorized_caller": "Unverified caller — no address shared",
        "general_request_handled": "Question answered",
        "out_of_scope": "Not about a delivery",
    }.get(outcome, "No outcome recorded")


def headline(provider: str | None, outcome: str) -> str:
    if outcome in {"unauthorized_caller", "out_of_scope"}:
        return "Someone called the delivery line"
    store = store_name(provider)
    return f"{store} rider called" if store else "A delivery partner called"


# Redaction -------------------------------------------------------------------


def redact_summary(value: object) -> str | None:
    """The agent's one-liner, safe to keep: no codes, no long numbers, no address.

    OTP-like codes and bare 4–8 digit codes become ``[hidden]``; any run of six or
    more digits is removed; a summary that still reads like an address is dropped.
    """

    if not isinstance(value, str):
        return None
    text = unicodedata.normalize("NFC", value)
    text = "".join(" " if unicodedata.category(char) in {"Cc", "Cf"} else char for char in text)
    text = " ".join(text.split())
    if not text:
        return None
    text = _LONG_DIGITS.sub("", text)
    text = redact_store_text(text)
    text = _HIDDEN_RUN.sub(" " + HIDDEN, text)
    text = " ".join(text.split()).strip(" ,;:-")
    if looks_like_address(text):
        return None
    if not text or text == HIDDEN:
        return None
    if len(text) > SUMMARY_MAX:
        text = text[: SUMMARY_MAX - 1].rstrip(" ,;:-") + "…"
    return text


def looks_like_address(text: str) -> bool:
    words = {match.group(0).casefold() for match in _ADDRESS_WORDS.finditer(text)}
    if len(words) >= 2:
        return True
    return bool(words) and bool(_HAS_DIGIT.search(text.replace(HIDDEN, "")))


def mask_caller(masked: object) -> str | None:
    """``+91******2590`` → ``•••• 2590``. Reads Sarvam's masked field only."""

    if not isinstance(masked, str):
        return None
    digits = re.sub(r"\D", "", masked)
    return f"•••• {digits[-4:]}" if len(digits) >= 4 else None


# Parsing ---------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedCall:
    interaction_id: str
    started_at: datetime
    ended_at: datetime | None
    provider: str | None
    outcome: str | None
    summary: str | None
    caller: str | None
    user_identifier: str | None
    cohort_id: str | None


def _when(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    # Sarvam returns UTC with no offset.
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _short_text(value: object, limit: int = 200) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= limit else None


def parse_interaction(item: object, deployment_id: str) -> ParsedCall | None:
    """One List interactions item → a call we may show, or None to ignore it."""

    if not isinstance(item, dict):
        return None
    interaction_id = item.get("interaction_id")
    if not isinstance(interaction_id, str) or not _INTERACTION_ID.fullmatch(interaction_id):
        return None
    if item.get("channel_direction") != "inbound" or item.get("deployment_id") != deployment_id:
        return None
    if item.get("is_debug_call") in {1, "1"}:
        return None
    started_at = _when(item.get("start_datetime"))
    if started_at is None:
        return None
    variables = item.get("agent_variables")
    variables = variables if isinstance(variables, dict) else {}
    outcome = variables.get("call_outcome")
    provider = variables.get("delivery_provider")
    provider = provider.strip().casefold() if isinstance(provider, str) else None
    return ParsedCall(
        interaction_id=interaction_id,
        started_at=started_at,
        ended_at=_when(item.get("end_datetime")),
        provider=provider if provider in STORE_NAMES else None,
        outcome=outcome if outcome in CALL_OUTCOMES else None,
        summary=redact_summary(variables.get("delivery_guidance_summary")),
        caller=mask_caller(item.get("user_contact_masked")),
        user_identifier=_short_text(item.get("user_identifier")),
        cohort_id=_short_text(item.get("cohort_id")),
    )


# Storage ---------------------------------------------------------------------


class InboundRiderCall(BaseModel):
    """What the app shows for one call."""

    id: str
    started_at: datetime
    provider: Provider | None = None
    outcome: CallOutcome
    summary: str | None = None
    caller: str | None = None


class InboundRiderCalls(BaseModel):
    calls: list[InboundRiderCall]


@dataclass(frozen=True)
class PreparedContext:
    user_id: str
    cohort_id: str
    prepared_at: datetime


def _call(record: InboundRiderCallRecord) -> InboundRiderCall:
    return InboundRiderCall(
        id=str(record.id),
        started_at=record.started_at if record.started_at.tzinfo else record.started_at.replace(tzinfo=UTC),
        provider=record.provider if record.provider in STORE_NAMES else None,  # type: ignore[arg-type]
        outcome=record.outcome if record.outcome in CALL_OUTCOMES else "no_decision",  # type: ignore[arg-type]
        summary=record.summary,
        caller=record.caller,
    )


class InboundCallStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def record_context(self, user_id: str, cohort_id: str, prepared_at: datetime) -> None:
        async with self.sessions() as session, session.begin():
            await insert_if_absent(
                session,
                InboundDeliveryContextRecord,
                {"id": uuid.uuid4(), "user_id": user_id, "cohort_id": cohort_id, "prepared_at": prepared_at},
                ["cohort_id"],
            )

    async def clear_contexts(self, user_id: str) -> int:
        """The delivery line was cleared: stop the context-gated call window for this user.

        An order that is still moving keeps its own window (``active_orders``)."""

        async with self.sessions() as session, session.begin():
            result = await session.execute(
                delete(InboundDeliveryContextRecord).where(InboundDeliveryContextRecord.user_id == user_id)
            )
            return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def contexts_since(self, since: datetime) -> list[PreparedContext]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(InboundDeliveryContextRecord).where(InboundDeliveryContextRecord.prepared_at >= since)
                )
            ).all()
        return [
            PreparedContext(row.user_id, row.cohort_id, _aware(row.prepared_at)) for row in rows
        ]

    async def cohort_owners(self, cohort_ids: set[str]) -> dict[str, str]:
        if not cohort_ids:
            return {}
        async with self.sessions() as session:
            rows = await session.execute(
                select(InboundDeliveryContextRecord.cohort_id, InboundDeliveryContextRecord.user_id).where(
                    InboundDeliveryContextRecord.cohort_id.in_(sorted(cohort_ids))
                )
            )
            return {cohort_id: user_id for cohort_id, user_id in rows.all()}

    async def add_call(self, user_id: str, call: ParsedCall, outcome: str, at: datetime) -> InboundRiderCall | None:
        """Save once per interaction; None when it was already saved (for anyone)."""

        claim = uuid.uuid4()
        async with self.sessions() as session, session.begin():
            await insert_if_absent(
                session,
                InboundRiderCallRecord,
                {
                    "id": claim,
                    "user_id": user_id,
                    "interaction_id": call.interaction_id,
                    "started_at": call.started_at,
                    "provider": call.provider,
                    "outcome": outcome,
                    "summary": call.summary,
                    "caller": call.caller,
                    "created_at": at,
                },
                ["interaction_id"],
            )
            record = await session.scalar(
                select(InboundRiderCallRecord).where(InboundRiderCallRecord.interaction_id == call.interaction_id)
            )
        if record is None or record.id != claim:
            return None
        return _call(record)

    async def known(self, interaction_ids: Iterable[str]) -> set[str]:
        ids = list(interaction_ids)
        if not ids:
            return set()
        async with self.sessions() as session:
            rows = await session.scalars(
                select(InboundRiderCallRecord.interaction_id).where(InboundRiderCallRecord.interaction_id.in_(ids))
            )
            return set(rows.all())

    async def list_calls(self, user_id: str, limit: int = CALLS_LISTED) -> list[InboundRiderCall]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(InboundRiderCallRecord)
                    .where(InboundRiderCallRecord.user_id == user_id)
                    .order_by(InboundRiderCallRecord.started_at.desc())
                    .limit(limit)
                )
            ).all()
        return [_call(row) for row in rows]

    async def delete_call(self, user_id: str, call_id: uuid.UUID) -> bool:
        async with self.sessions() as session, session.begin():
            result = await session.execute(
                delete(InboundRiderCallRecord).where(
                    InboundRiderCallRecord.user_id == user_id, InboundRiderCallRecord.id == call_id
                )
            )
            return bool(result.rowcount)  # type: ignore[attr-defined]

    async def delete_all(self, user_id: str) -> int:
        """Every call summary and prepared-context time for this user."""

        async with self.sessions() as session, session.begin():
            calls = await session.execute(
                delete(InboundRiderCallRecord).where(InboundRiderCallRecord.user_id == user_id)
            )
            await session.execute(
                delete(InboundDeliveryContextRecord).where(InboundDeliveryContextRecord.user_id == user_id)
            )
            return int(calls.rowcount or 0)  # type: ignore[attr-defined]


async def delete_user_inbound_calls(session: AsyncSession, user_id: str) -> None:
    """For "forget everything": same deletion inside the caller's transaction."""

    await session.execute(delete(InboundRiderCallRecord).where(InboundRiderCallRecord.user_id == user_id))
    await session.execute(
        delete(InboundDeliveryContextRecord).where(InboundDeliveryContextRecord.user_id == user_id)
    )


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


# Monitor ---------------------------------------------------------------------


@dataclass
class Watch:
    """One user's reason to poll: calls at or after ``since`` may be theirs."""

    user_id: str
    since: datetime
    user_key: str
    cohort_ids: set[str] = field(default_factory=set)


ActiveOrders = Callable[[], Iterable[tuple[str, datetime]]]
UserKey = Callable[[str], str]


class Notifier:  # pragma: no cover - structural type only
    async def send(self, user_id: str, payload: Any, *, dedupe_key: str | None = None) -> Any: ...


class RiderCallMonitor:
    """Read-only poll of the inbound agent's interactions; one request per tick at most."""

    def __init__(
        self,
        settings: Settings,
        store: InboundCallStore,
        *,
        active_orders: ActiveOrders,
        user_key: UserKey,
        notifier: Notifier | None = None,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.settings = settings
        self.store = store
        self.active_orders = active_orders
        self.user_key = user_key
        self.notifier = notifier
        self.client = client
        self.clock = clock
        self.failures = 0
        self._last_request_at: datetime | None = None
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    # Configuration ---------------------------------------------------------

    @property
    def configured(self) -> bool:
        settings = self.settings
        return bool(
            settings.sarvam_call_monitor_enabled
            and settings.sarvam_voice_agent_api_key.get_secret_value().strip()
            and settings.sarvam_voice_agent_org_id
            and settings.sarvam_voice_agent_workspace_id
            and settings.sarvam_inbound_agent_id
            and settings.sarvam_inbound_deployment_id
        )

    def _url(self) -> str:
        settings = self.settings
        return (
            f"{ANALYTICS_URL}/{settings.sarvam_voice_agent_org_id}/{settings.sarvam_voice_agent_workspace_id}"
            f"/{settings.sarvam_inbound_agent_id}/interactions"
        )

    @asynccontextmanager
    async def _http(self) -> AsyncIterator[httpx.AsyncClient]:
        if self.client is not None:
            yield self.client
        else:
            async with httpx.AsyncClient(timeout=12.0) as client:
                yield client

    # Gating ----------------------------------------------------------------

    async def watches(self, now: datetime) -> list[Watch]:
        by_user: dict[str, Watch] = {}

        def watch(user_id: str, since: datetime) -> Watch:
            current = by_user.get(user_id)
            if current is None:
                current = by_user[user_id] = Watch(user_id, since, self.user_key(user_id))
            elif since < current.since:
                current.since = since
            return current

        for context in await self.store.contexts_since(now - CONTEXT_WINDOW):
            watch(context.user_id, context.prepared_at).cohort_ids.add(context.cohort_id)
        for user_id, placed_at in self.active_orders():
            placed_at = _aware(placed_at)
            if now - placed_at <= ORDER_WINDOW:
                watch(user_id, placed_at)
        return list(by_user.values())

    # One tick --------------------------------------------------------------

    async def tick(self) -> float:
        """Poll once if anyone is waiting; return the seconds until the next tick."""

        if not self.configured:
            return POLL_SECONDS
        now = self.clock()
        watches = await self.watches(now)
        if not watches:
            return POLL_SECONDS
        if self._last_request_at is not None and (now - self._last_request_at).total_seconds() < POLL_SECONDS:
            return POLL_SECONDS - (now - self._last_request_at).total_seconds()
        self._last_request_at = now
        start = min(item.since for item in watches) - timedelta(minutes=1)
        params = {
            "start_datetime": start.isoformat(timespec="seconds"),
            "end_datetime": (now + timedelta(minutes=1)).isoformat(timespec="seconds"),
            "sort_by": "start_datetime",
            "sort_order": "desc",
            "limit": str(PAGE_LIMIT),
            "filter_conditions": json.dumps(
                [{"field": "channel_direction", "operator": "equals", "value": "inbound"}]
            ),
        }
        headers = {"X-API-Key": self.settings.sarvam_voice_agent_api_key.get_secret_value()}
        try:
            async with self._http() as client:
                response = await client.get(self._url(), params=params, headers=headers)
        except httpx.HTTPError as exc:
            return self._backoff("unreachable", error=type(exc).__name__)
        if response.status_code in {401, 403}:
            self.failures += 1
            self._log("auth_failed", status=response.status_code)
            return float(AUTH_BACKOFF_SECONDS)
        if response.status_code == 429:
            return self._backoff("rate_limited", status=429, at_least=_retry_after(response))
        if response.status_code != 200:
            return self._backoff("provider_error", status=response.status_code)
        try:
            payload = response.json()
            items = payload["items"] if isinstance(payload, dict) else None
            if not isinstance(items, list):
                raise ValueError("items")
        except (ValueError, KeyError, TypeError):
            return self._backoff("malformed", status=200)
        self.failures = 0
        saved = await self._handle(items, watches, now)
        self._log("ok", status=200, items=len(items), saved=saved, watching=len(watches))
        return POLL_SECONDS

    async def _handle(self, items: list[object], watches: list[Watch], now: datetime) -> int:
        calls = [
            call
            for call in (parse_interaction(item, self.settings.sarvam_inbound_deployment_id) for item in items)
            if call is not None and call.interaction_id not in self._seen
        ]
        if not calls:
            return 0
        already = await self.store.known(call.interaction_id for call in calls)
        owners = await self.store.cohort_owners({call.cohort_id for call in calls if call.cohort_id})
        saved = 0
        for call in sorted(calls, key=lambda item: item.started_at):
            if call.interaction_id in already:
                self._remember(call.interaction_id)
                continue
            outcome = call.outcome
            if outcome is None:
                finished = call.ended_at or call.started_at
                if now - finished < OUTCOME_GRACE:
                    continue  # post-call analysis not in yet: look again next tick
                outcome = "no_decision"
            owner = attribute(call, watches, owners)
            if owner is None:
                continue
            record = await self.store.add_call(owner, call, outcome, now)
            self._remember(call.interaction_id)
            if record is None:
                continue
            saved += 1
            await self._notify(owner, record)
        return saved

    async def _notify(self, user_id: str, call: InboundRiderCall) -> None:
        if self.notifier is None:
            return
        payload = messages.inbound_rider_called(
            call.started_at,
            headline(call.provider, call.outcome),
            outcome_text(call.outcome, call.provider),
            call_id=call.id,
        )
        try:
            await self.notifier.send(user_id, payload, dedupe_key=f"rider_called:inbound:{call.id}")
        except Exception as exc:  # noqa: BLE001 - a notification never stops the monitor
            logger.warning("event=rider_call_push_failed error=%s", type(exc).__name__)

    def _remember(self, interaction_id: str) -> None:
        self._seen[interaction_id] = None
        while len(self._seen) > SEEN_MEMORY:
            self._seen.popitem(last=False)

    def _backoff(self, result: str, *, status: int | None = None, error: str | None = None, at_least: float = 0.0) -> float:
        self.failures += 1
        delay = min(MAX_BACKOFF_SECONDS, POLL_SECONDS * (2 ** self.failures))
        delay = float(min(MAX_BACKOFF_SECONDS, max(delay, at_least)))
        self._log(result, status=status, error=error, retry_in=int(delay))
        return delay

    def _log(self, result: str, **fields: object) -> None:
        details = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
        logger.info("event=rider_call_monitor result=%s %s", result, details)

    # Loop ------------------------------------------------------------------

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="rider-call-monitor")

    async def aclose(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                delay = await self.tick()
            except Exception as exc:  # noqa: BLE001 - e.g. the database is briefly down
                delay = self._backoff("cycle_failed", error=type(exc).__name__)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=max(delay, 1.0))
            except TimeoutError:
                pass


def attribute(call: ParsedCall, watches: list[Watch], cohort_owners: dict[str, str] | None = None) -> str | None:
    """The one user this call belongs to, or None.

    A call from a cohort a user prepared, or carrying their Sarvam user
    identifier, is theirs (only while they are waiting for calls). A call with
    neither (an unknown caller) is shown only when exactly one user was waiting
    at that time: the line serves one household. A cohort GharSaathi does not
    know is never attributed.
    """

    eligible = [watch for watch in watches if call.started_at >= watch.since]
    eligible_users = {watch.user_id for watch in eligible}
    if call.cohort_id:
        owner = (cohort_owners or {}).get(call.cohort_id)
        if owner is None:
            owner = next((watch.user_id for watch in watches if call.cohort_id in watch.cohort_ids), None)
        return owner if owner in eligible_users else None
    if call.user_identifier:
        for watch in watches:
            if call.user_identifier == watch.user_key:
                return watch.user_id if watch.user_id in eligible_users else None
    return eligible_users.pop() if len(eligible_users) == 1 else None


def _retry_after(response: httpx.Response) -> float:
    try:
        return float(max(0, int(response.headers.get("Retry-After", "60"))))
    except ValueError:
        return 60.0
