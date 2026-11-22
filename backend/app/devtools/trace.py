"""Opt-in local turn traces (``DEV_TRACE=true``; docs/DEV_TOOLS.md).

One JSONL record per typed-chat turn or voice delegation: which model rounds ran,
each tool's name, argument keys with short redacted values, result status and
milliseconds, token usage when the model reports it, and the total. Records
never hold message text, replies, full tool outputs, provider payloads, images,
tokens or secrets. They are written under ``.run/traces/`` (gitignored) and
rotated to the last ``DEV_TRACE_KEEP`` turns. Off by default.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.voice.timing import ms_since

TRACE_FILE = "turns.jsonl"
TRACE_VERSION = 1
MAX_VALUE_CHARS = 40
MAX_ARG_KEYS = 12
REDACTED = "‹redacted›"
# Argument-name parts whose values are never written (address details, contact
# numbers, payment data, credentials). Matched per ``_``-separated part, so
# "spin_id" is kept and "pin" / "contact_number" are not.
SENSITIVE_PARTS = frozenset(
    {
        "phone", "contact", "mobile", "otp", "pin", "pincode", "upi", "vpa", "card", "cvv",
        "token", "password", "secret", "key", "email", "flat", "house", "building", "street",
        "landmark", "floor", "latitude", "longitude", "lat", "lng", "receiver", "recipient",
    }
)
# Tools whose free-text arguments are an address the user is typing.
REDACT_ALL_STRINGS = frozenset({"open_address_form"})
_PHONE_LIKE = re.compile(r"^\+?[\d\s()-]{6,}$")


def _sensitive(key: str) -> bool:
    return any(part in SENSITIVE_PARTS for part in re.split(r"[_\W]+", key.lower()) if part)


def short_value(value: Any) -> Any:
    """A short, content-light stand-in for one argument value."""

    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value
    if isinstance(value, str):
        text = " ".join(value.split())
        if _PHONE_LIKE.match(text) and sum(ch.isdigit() for ch in text) >= 6:
            return REDACTED
        return text if len(text) <= MAX_VALUE_CHARS else text[: MAX_VALUE_CHARS - 1] + "…"
    if isinstance(value, list | tuple):
        if all(isinstance(item, str | int | float | bool) for item in value) and len(value) <= 3:
            return [short_value(item) for item in value]
        return f"[{len(value)} items]"
    if isinstance(value, dict):
        return "{" + ",".join(sorted(str(key) for key in value)[:6]) + "}"
    return type(value).__name__


def summarize_arguments(tool: str, arguments: str | Mapping[str, Any] | None) -> dict[str, Any]:
    """Argument keys with short values; sensitive keys and address text are redacted."""

    parsed: Any = arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            return {"_unparsed_chars": len(arguments)}
    if not isinstance(parsed, Mapping):
        return {"_type": type(parsed).__name__}
    summary: dict[str, Any] = {}
    for key in list(parsed)[:MAX_ARG_KEYS]:
        value = parsed[key]
        name = str(key)[:40]
        if _sensitive(name) or (tool in REDACT_ALL_STRINGS and isinstance(value, str) and value):
            summary[name] = REDACTED
        else:
            summary[name] = short_value(value)
    if len(parsed) > MAX_ARG_KEYS:
        summary["_more_keys"] = len(parsed) - MAX_ARG_KEYS
    return summary


def summarize_result(output: Mapping[str, Any] | None) -> tuple[str, dict[str, int]]:
    """The result status and the sizes of its top-level lists (never their content)."""

    if not isinstance(output, Mapping):
        return "none", {}
    status = output.get("status")
    counts = {str(key)[:40]: len(value) for key, value in output.items() if isinstance(value, list)}
    return (str(status)[:40] if status is not None else "-"), dict(list(counts.items())[:4])


def _get(source: Any, name: str) -> Any:
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def usage_of(usage: Any) -> dict[str, int]:
    """Token counts from a Responses ``usage`` object or dict; empty when absent."""

    if usage is None:
        return {}
    tokens: dict[str, int] = {}
    for key, name in (("input", "input_tokens"), ("output", "output_tokens")):
        value = _get(usage, name)
        if isinstance(value, int) and not isinstance(value, bool):
            tokens[key] = value
    cached = _get(_get(usage, "input_tokens_details"), "cached_tokens")
    reasoning = _get(_get(usage, "output_tokens_details"), "reasoning_tokens")
    if isinstance(cached, int) and not isinstance(cached, bool):
        tokens["cached"] = cached
    if isinstance(reasoning, int) and not isinstance(reasoning, bool):
        tokens["reasoning"] = reasoning
    return tokens


class TurnTrace:
    """Collects one turn's rounds and tool calls; ``finish`` returns the record."""

    def __init__(
        self,
        channel: str,
        *,
        model: str,
        prompt_version: str,
        conversation_id: str | None = None,
        session_id: str | None = None,
        sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.turn_id = uuid.uuid4().hex[:12]
        self.channel = channel
        self.model = model
        self.prompt_version = prompt_version
        self.conversation_id = conversation_id
        self.session_id = session_id
        self.started_at = datetime.now(UTC)
        self._started = time.perf_counter()
        self.rounds: list[dict[str, Any]] = []
        self._sink = sink
        self.finished = False

    def _round(self, number: int) -> dict[str, Any]:
        while len(self.rounds) < number:
            self.rounds.append({"round": len(self.rounds) + 1, "model_ms": 0.0, "tools": []})
        return self.rounds[number - 1]

    def model_round(self, model_ms: float, usage: Any = None, *, round_number: int | None = None) -> int:
        """Record one model response; returns its round number (1-based).

        Without ``round_number`` the response is the next round (typed chat runs
        rounds in order); voice passes the round its tool calls were filed under.
        """

        number = round_number or len(self.rounds) + 1
        entry = self._round(number)
        entry["model_ms"] = round(model_ms, 1)
        tokens = usage_of(usage)
        if tokens:
            entry["tokens"] = tokens
        return number

    def tool(
        self,
        name: str,
        arguments: str | Mapping[str, Any] | None,
        output: Mapping[str, Any] | None,
        ms: float,
        *,
        round_number: int | None = None,
        stages: Mapping[str, float] | None = None,
        status: str | None = None,
    ) -> None:
        entry_round = self._round(max(round_number or len(self.rounds), 1))
        result_status, counts = summarize_result(output)
        entry: dict[str, Any] = {
            "name": (name or "unknown")[:64],
            "args": summarize_arguments(name, arguments),
            "status": status or result_status,
            "ms": round(ms, 1),
        }
        if counts:
            entry["counts"] = counts
        if stages:
            entry["stages"] = {str(key)[:40]: round(float(value), 1) for key, value in list(stages.items())[:10]}
        entry_round["tools"].append(entry)

    def finish(self, outcome: str) -> dict[str, Any]:
        self.finished = True
        tokens: dict[str, int] = {}
        for entry in self.rounds:
            for key, value in entry.get("tokens", {}).items():
                tokens[key] = tokens.get(key, 0) + value
        tools = [tool for entry in self.rounds for tool in entry["tools"]]
        record: dict[str, Any] = {
            "v": TRACE_VERSION,
            "turn_id": self.turn_id,
            "at": self.started_at.isoformat(timespec="seconds"),
            "channel": self.channel,
            "conversation_id": self.conversation_id,
            "session_id": self.session_id,
            "prompt_version": self.prompt_version,
            "model": self.model,
            "outcome": outcome[:40],
            "total_ms": ms_since(self._started),
            "rounds": self.rounds,
            "totals": {
                "rounds": len(self.rounds),
                "tools": len(tools),
                "model_ms": round(sum(entry["model_ms"] for entry in self.rounds), 1),
                "tool_ms": round(sum(tool["ms"] for tool in tools), 1),
                **({"tokens": tokens} if tokens else {}),
            },
        }
        if self._sink is not None:
            self._sink(record)
        return record


class TraceWriter:
    """Append-only JSONL with rotation to the newest ``keep`` records."""

    def __init__(self, directory: Path, keep: int = 500) -> None:
        self.directory = directory
        self.path = directory / TRACE_FILE
        self.keep = keep
        self._lock = threading.Lock()
        self._count: int | None = None

    def write(self, record: Mapping[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self._count is None:
                self._count = self._line_count()
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            self._count += 1
            if self._count > self.keep + max(1, self.keep // 5):
                self._rotate()

    def _line_count(self) -> int:
        try:
            with self.path.open(encoding="utf-8") as handle:
                return sum(1 for _ in handle)
        except FileNotFoundError:
            return 0

    def _rotate(self) -> None:
        lines = self.path.read_text(encoding="utf-8").splitlines()[-self.keep :]
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)
        self._count = len(lines)

    def read(self, limit: int | None = None) -> list[dict[str, Any]]:
        return read_traces(self.directory, limit)


def read_traces(directory: Path, limit: int | None = None) -> list[dict[str, Any]]:
    """Trace records, oldest first; unreadable lines are skipped."""

    path = directory / TRACE_FILE
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    records: list[dict[str, Any]] = []
    for line in lines[-limit:] if limit else lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


_writers: dict[Path, TraceWriter] = {}
_writers_lock = threading.Lock()


def trace_writer(settings: Any) -> TraceWriter | None:
    """The process-wide writer when ``DEV_TRACE`` is on, else None."""

    if not getattr(settings, "dev_trace", False):
        return None
    directory = Path(settings.dev_trace_dir)
    with _writers_lock:
        writer = _writers.get(directory)
        if writer is None:
            writer = _writers[directory] = TraceWriter(directory, settings.dev_trace_keep)
        return writer


def start_trace(settings: Any, channel: str, **fields: Any) -> TurnTrace | None:
    """A new trace that writes itself on ``finish``, or None when tracing is off."""

    writer = trace_writer(settings)
    if writer is None:
        return None
    from app.voice.prompts import VOICE_PROMPT_VERSION

    def sink(record: dict[str, Any]) -> None:
        try:
            writer.write(record)
        except OSError:
            pass  # A dev diagnostic must never break a turn.

    return TurnTrace(
        channel,
        model=str(getattr(settings, "live_backend_model", "")),
        prompt_version=VOICE_PROMPT_VERSION,
        sink=sink,
        **fields,
    )
