"""Offline-testable safety harness for the opt-in live provider smoke suite.

Nothing in this module talks to a provider by itself. It provides:

* the opt-in gate (``live_skip_reason``),
* a tool-call guard that makes order confirmation, payment and checkout calls
  structurally impossible from the smoke suite (``GuardedToolClient``),
* a rate-limit tripwire that halts the whole run on the first 429, and
* a content-free schema reporter (``shape_of``) that records key paths, JSON
  types and counts but never a provider value.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pytest

from app.commerce.swiggy.errors import SwiggyRateLimited
from app.commerce.zepto.errors import ZeptoRateLimited

LIVE_ENV = "GHARSAATHI_LIVE_SMOKE"
CART_ENV = "GHARSAATHI_LIVE_SMOKE_CART"
CART_RESTORE_ENV = "GHARSAATHI_LIVE_SMOKE_CART_RESTORE"
RUN_LIVE_OPTION = "--run-live"

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = BACKEND_ROOT / ".live-smoke"
FIXTURE_DIR = BACKEND_ROOT / "tests" / "fixtures"

Provider = Literal["swiggy", "zepto"]
Tier = Literal["read", "cart"]


# --------------------------------------------------------------------------
# Opt-in gate
# --------------------------------------------------------------------------


def _enabled(env: Mapping[str, str], name: str) -> bool:
    return env.get(name, "").strip() == "1"


def live_skip_reason(
    *, run_live_flag: bool, env: Mapping[str, str], cart: bool
) -> str | None:
    """Return why a live test must be skipped, or ``None`` when it may run.

    Both the command-line flag and the environment variable are required so
    neither a stray shell export nor a copied pytest command can reach a
    provider on its own. Cart-mutating checks need a second, separate opt-in.
    """

    if not run_live_flag:
        return f"live smoke test: pass {RUN_LIVE_OPTION} (and {LIVE_ENV}=1) to run"
    if not _enabled(env, LIVE_ENV):
        return f"live smoke test: set {LIVE_ENV}=1 (and pass {RUN_LIVE_OPTION}) to run"
    if cart and not _enabled(env, CART_ENV):
        return (
            f"cart-mutating live smoke test: also set {CART_ENV}=1 "
            "(this replaces the Zepto cart)"
        )
    return None


# --------------------------------------------------------------------------
# Forbidden-call guard
# --------------------------------------------------------------------------


class ForbiddenLiveCall(BaseException):
    """A smoke check attempted a call the suite must never make.

    Deliberately a ``BaseException``: provider wrappers such as
    ``ZeptoQuoteProvider.quote_unlocked`` catch ``Exception`` and would
    otherwise turn this hard stop into an ordinary "provider unavailable".
    """


class LiveSmokeHalted(Exception):
    """The run already hit a provider rate limit; no further calls are made."""


READ_ONLY_TOOLS: dict[str, frozenset[str]] = {
    "swiggy": frozenset({"get_addresses", "search_products"}),
    "zepto": frozenset(
        {
            "list_saved_addresses",
            "select_saved_address",
            "search_products",
            "search_multiple_products",
        }
    ),
}
CART_TOOLS: dict[str, frozenset[str]] = {
    "swiggy": frozenset(),
    "zepto": frozenset({"view_cart", "update_cart", "create_order"}),
}
CREATE_ORDER_ALLOWED_KEYS = frozenset({"confirmOrder", "userAddressId"})
# Defence in depth: even an allow-listed name is refused if it looks like a
# confirmation, payment or checkout capability.
FORBIDDEN_NAME = re.compile(
    r"(confirm|checkout|pay|place|wallet|upi|cancel|refund|reserve)", re.IGNORECASE
)


def _confirm_flags(value: object, path: str = "") -> Iterable[tuple[str, object]]:
    if isinstance(value, dict):
        for key, item in value.items():
            here = f"{path}.{key}" if path else str(key)
            if "confirm" in str(key).casefold():
                yield here, item
            yield from _confirm_flags(item, here)
    elif isinstance(value, list):
        for item in value:
            yield from _confirm_flags(item, f"{path}[]")


def assert_call_permitted(
    provider: str,
    tier: str,
    name: str,
    arguments: Mapping[str, Any],
    env: Mapping[str, str],
) -> None:
    """Raise ``ForbiddenLiveCall`` unless this exact call is safe for the tier."""

    if tier not in ("read", "cart"):
        raise ForbiddenLiveCall(f"Unknown live smoke tier {tier!r}.")
    if FORBIDDEN_NAME.search(name):
        raise ForbiddenLiveCall(f"{provider}.{name} is never callable from live smoke.")
    allowed = set(READ_ONLY_TOOLS.get(provider, frozenset()))
    if tier == "cart":
        if not _enabled(env, CART_ENV):
            raise ForbiddenLiveCall(
                f"{provider}.{name}: cart tier requires {CART_ENV}=1 at call time."
            )
        allowed |= CART_TOOLS.get(provider, frozenset())
    if name not in allowed:
        raise ForbiddenLiveCall(
            f"{provider}.{name} is not allow-listed for the {tier} live smoke tier."
        )
    for path, flag in _confirm_flags(dict(arguments)):
        if flag is not False:
            raise ForbiddenLiveCall(
                f"{provider}.{name}: {path} must be exactly False in live smoke."
            )
    if name == "create_order":
        if arguments.get("confirmOrder") is not False:
            raise ForbiddenLiveCall("create_order requires confirmOrder=False exactly.")
        extra = set(arguments) - CREATE_ORDER_ALLOWED_KEYS
        if extra:
            raise ForbiddenLiveCall(
                f"create_order received unexpected arguments: {sorted(extra)}"
            )


# --------------------------------------------------------------------------
# Rate-limit tripwire
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RateLimitTrip:
    provider: str
    operation: str
    retry_after_seconds: int | None
    source: str


@dataclass
class RateLimitTripwire:
    trip: RateLimitTrip | None = None

    def record(self, provider: str, operation: str, error: BaseException) -> None:
        if self.trip is not None:
            return
        retry_after = getattr(error, "retry_after_seconds", None)
        self.trip = RateLimitTrip(
            provider=provider,
            operation=operation,
            retry_after_seconds=retry_after if isinstance(retry_after, int) else None,
            source=type(error).__name__,
        )

    def record_cooldown(self, provider: str, remaining_seconds: int) -> None:
        if self.trip is None:
            self.trip = RateLimitTrip(
                provider, "persisted_cooldown", remaining_seconds, "stored_cooldown"
            )

    def check(self) -> None:
        if self.trip is not None:
            raise LiveSmokeHalted(self.halt_message())

    def halt_message(self) -> str:
        trip = self.trip
        if trip is None:
            return ""
        retry = (
            f"Retry-After {trip.retry_after_seconds}s"
            if trip.retry_after_seconds is not None
            else "Retry-After not provided (client applies its own cooldown)"
        )
        return (
            f"LIVE SMOKE HALTED: {trip.provider} rate-limited during {trip.operation}; "
            f"{retry}. No further provider calls were made. Wait before re-running."
        )

    def stop_session_if_tripped(self) -> None:
        if self.trip is not None:
            pytest.exit(self.halt_message(), returncode=3)


RATE_LIMIT_ERRORS: tuple[type[BaseException], ...] = (SwiggyRateLimited, ZeptoRateLimited)


# --------------------------------------------------------------------------
# Content-free schema reporter
# --------------------------------------------------------------------------

_SAFE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]{0,47}$")
_DIGIT_RUN = re.compile(r"\d{4,}")


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "other"


def safe_key(key: object) -> str:
    """Keep schema-like keys; collapse anything that could be data to ``<key>``.

    Providers occasionally key objects by identifiers, phone numbers or names.
    Such keys are values in disguise and are not written to the report.
    """

    text = str(key)
    if not _SAFE_KEY.match(text) or _DIGIT_RUN.search(text) or "@" in text:
        return "<key>"
    return text


def shape_of(value: object) -> dict[str, dict[str, Any]]:
    """Return ``{key_path: {"types": [...], "seen": n, ...}}`` with no values.

    Arrays contribute ``min_len``/``max_len`` counts; strings contribute
    nothing beyond their type, so no length can leak the size of a name.
    """

    shapes: dict[str, dict[str, Any]] = {}

    def visit(item: object, path: str) -> None:
        entry = shapes.setdefault(path or "$", {"types": set(), "seen": 0})
        entry["types"].add(_json_type(item))
        entry["seen"] += 1
        if isinstance(item, dict):
            for key, child in item.items():
                segment = safe_key(key)
                visit(child, f"{path}.{segment}" if path else segment)
        elif isinstance(item, list):
            length = len(item)
            entry["min_len"] = min(entry.get("min_len", length), length)
            entry["max_len"] = max(entry.get("max_len", length), length)
            for child in item:
                visit(child, f"{path}[]")

    visit(value, "")
    return {
        path: {**entry, "types": sorted(entry["types"])}
        for path, entry in sorted(shapes.items())
    }


@dataclass
class SmokeRecorder:
    """Collect content-free observations and write them to ``.live-smoke/``."""

    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    responses: list[dict[str, Any]] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)

    def record_response(self, provider: str, tool: str, payload: object) -> None:
        self.responses.append(
            {"provider": provider, "tool": tool, "shape": shape_of(payload)}
        )

    def record_error(self, provider: str, tool: str, error: BaseException) -> None:
        self.responses.append(
            {"provider": provider, "tool": tool, "error_type": type(error).__name__}
        )

    def record_check(self, provider: str, check: str, **facts: Any) -> None:
        """Facts must be counts, booleans, type names or tool names only."""

        for key, item in facts.items():
            if not isinstance(item, (bool, int, float, type(None), list, dict, str)):
                raise TypeError(f"Unsupported report fact {key}.")
        self.checks.append({"provider": provider, "check": check, **facts})

    def report(self, halt: str | None = None) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "halt": halt,
            "checks": self.checks,
            "responses": self.responses,
        }

    def write(self, halt: str | None = None, directory: Path = REPORT_DIR) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        stamp = self.started_at.strftime("%Y%m%dT%H%M%SZ")
        body = json.dumps(self.report(halt), indent=2, sort_keys=True)
        path = directory / f"report-{stamp}.json"
        path.write_text(body + "\n")
        (directory / "latest.json").write_text(body + "\n")
        return path


# --------------------------------------------------------------------------
# Guarded client
# --------------------------------------------------------------------------


class GuardedToolClient:
    """Wrap a real provider MCP client; the only path the suite has to it.

    Exposes exactly the client surface the real services/adapters use. Every
    ``call_tool`` is checked by ``assert_call_permitted`` *before* the inner
    client is touched, and every response is recorded as a content-free shape.
    """

    def __init__(
        self,
        inner: Any,
        *,
        provider: str,
        tier: str,
        tripwire: RateLimitTripwire,
        recorder: SmokeRecorder,
        env: Mapping[str, str],
    ) -> None:
        self._inner = inner
        self.provider = provider
        self.tier = tier
        self._tripwire = tripwire
        self._recorder = recorder
        self._env = env
        self.calls: list[str] = []
        self.last_payloads: dict[str, dict[str, Any]] = {}

    async def _guarded(self, operation: str, run: Any) -> Any:
        self._tripwire.check()
        self.calls.append(operation)
        try:
            return await run()
        except RATE_LIMIT_ERRORS as exc:
            self._tripwire.record(self.provider, operation, exc)
            self._recorder.record_error(self.provider, operation, exc)
            raise LiveSmokeHalted(self._tripwire.halt_message()) from exc

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        retry_on_auth: bool = True,
    ) -> dict[str, Any]:
        assert_call_permitted(self.provider, self.tier, name, arguments, self._env)
        checked = copy.deepcopy(arguments)

        async def run() -> dict[str, Any]:
            return await self._inner.call_tool(
                name, checked, retry_on_auth=retry_on_auth
            )

        try:
            payload = await self._guarded(name, run)
        except LiveSmokeHalted:
            raise
        except Exception as exc:
            self._recorder.record_error(self.provider, name, exc)
            raise
        self._recorder.record_response(self.provider, name, payload)
        self.last_payloads[name] = payload
        return payload

    async def list_tools(self) -> Any:
        return await self._guarded("tools/list", self._inner.list_tools)

    async def prepare_session(self) -> int:
        return await self._guarded("prepare_session", self._inner.prepare_session)

    @property
    def session_generation(self) -> int:
        return self._inner.session_generation

    async def aclose(self) -> None:
        await self._inner.aclose()


def guarded_for_tier(client: GuardedToolClient, tier: str) -> GuardedToolClient:
    """A sibling guard sharing the same transport but with a different tier."""

    sibling = GuardedToolClient(
        client._inner,
        provider=client.provider,
        tier=tier,
        tripwire=client._tripwire,
        recorder=client._recorder,
        env=client._env,
    )
    return sibling


# --------------------------------------------------------------------------
# Catalogue comparison
# --------------------------------------------------------------------------

_ANNOTATION_KEYS = frozenset({"description", "title", "examples"})


def structural_schema(schema: object, *, in_properties: bool = False) -> object:
    """Drop human-readable annotations but keep properties named like them."""

    if isinstance(schema, dict):
        result: dict[str, object] = {}
        for key, value in schema.items():
            if not in_properties and key in _ANNOTATION_KEYS:
                continue
            result[key] = structural_schema(value, in_properties=key == "properties" and not in_properties)
        return result
    if isinstance(schema, list):
        return [structural_schema(item) for item in schema]
    return schema


def catalogue_entries(tools: Iterable[Any]) -> list[dict[str, Any]]:
    """Snapshot format matching ``zepto_tools_catalogue_2026-11-21.json``."""

    return sorted(
        ({"name": tool.name, "input_schema": tool.input_schema} for tool in tools),
        key=lambda entry: entry["name"],
    )


def compare_catalogues(
    snapshot: list[dict[str, Any]], live: list[dict[str, Any]]
) -> dict[str, list[str]]:
    expected = {entry["name"]: entry["input_schema"] for entry in snapshot}
    actual = {entry["name"]: entry["input_schema"] for entry in live}
    changed: list[str] = []
    annotations: list[str] = []
    for name in sorted(set(expected) & set(actual)):
        if structural_schema(expected[name]) != structural_schema(actual[name]):
            changed.append(name)
        elif expected[name] != actual[name]:
            annotations.append(name)
    return {
        "added": sorted(set(actual) - set(expected)),
        "removed": sorted(set(expected) - set(actual)),
        "schema_changed": changed,
        "description_only_changed": annotations,
    }


def latest_snapshot(provider: str, directory: Path = FIXTURE_DIR) -> Path | None:
    candidates = sorted(directory.glob(f"{provider}_tools_catalogue_*.json"))
    return candidates[-1] if candidates else None
