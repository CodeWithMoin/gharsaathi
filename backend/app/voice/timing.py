"""Content-free stage timings for shopping turns (VOICE_EVENT_LOG only).

Tool code calls ``record(stage, ms)``; it is a no-op unless the caller opened a
``collect()`` scope, so production turns pay nothing when the flag is off.
Only stage names and milliseconds are ever logged, never queries or results.
"""

import contextvars
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager


logger = logging.getLogger(__name__)

_stages: contextvars.ContextVar[dict[str, float] | None] = contextvars.ContextVar(
    "turn_stage_timings", default=None
)


def ms_since(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def record(stage: str, ms: float) -> None:
    stages = _stages.get()
    if stages is not None:
        stages[stage] = round(stages.get(stage, 0.0) + ms, 1)


@contextmanager
def stage(name: str) -> Iterator[None]:
    if _stages.get() is None:
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        record(name, ms_since(started))


@contextmanager
def collect(enabled: bool = True) -> Iterator[dict[str, float]]:
    """Collect stages recorded in this task and tasks it starts (asyncio copies the context)."""

    stages: dict[str, float] = {}
    if not enabled:
        yield stages
        return
    token = _stages.set(stages)
    try:
        yield stages
    finally:
        _stages.reset(token)


def log_timing(event: str, fields: dict[str, float | int | str]) -> None:
    # WARNING so the opt-in diagnostic shows without app logging configuration.
    logger.warning(
        "event=%s %s",
        event,
        " ".join(
            f"{key}={value:.0f}" if isinstance(value, float) else f"{key}={value}"
            for key, value in fields.items()
        ),
    )
