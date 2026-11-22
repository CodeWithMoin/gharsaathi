import logging
import re
from typing import Any


OAUTH_CALLBACK_PATHS = {
    "/api/swiggy/oauth/callback",
    "/api/zepto/oauth/callback",
    "/callback",
}
# Voice event streams carry a per-session capability token in the query string.
VOICE_EVENTS_PATH = re.compile(r"^(/api/voice/sessions/[^/?]+/events)\?")
# Backwards-compatible name used by existing tests and integrations.
OAUTH_CALLBACK_PATH = "/api/swiggy/oauth/callback"


class RedactOAuthCallbackFilter(logging.Filter):
    """Keep OAuth codes/state and voice event tokens out of Uvicorn access logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        arguments = record.args
        if not isinstance(arguments, tuple) or len(arguments) < 3:
            return True
        request_path = arguments[2]
        if not isinstance(request_path, str):
            return True
        callback_path = next(
            (
                path
                for path in OAUTH_CALLBACK_PATHS
                if request_path.startswith(f"{path}?")
            ),
            None,
        )
        if callback_path is None:
            voice_events = VOICE_EVENTS_PATH.match(request_path)
            callback_path = voice_events.group(1) if voice_events else None
        if callback_path is not None:
            mutable: list[Any] = list(arguments)
            mutable[2] = f"{callback_path}?[REDACTED]"
            record.args = tuple(mutable)
        return True


# Rider assist stream URLs carry a per-call signed token in the path.
RIDER_STREAM_TOKEN = re.compile(r"(/api/rider/stream/[^/\s?\"]+/)[^/\s?\"]+")


class RedactRiderStreamFilter(logging.Filter):
    """Hide the signed token in rider stream paths (access and WebSocket logs)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                RIDER_STREAM_TOKEN.sub(r"\1[REDACTED]", value) if isinstance(value, str) else value
                for value in record.args
            )
        if isinstance(record.msg, str):
            record.msg = RIDER_STREAM_TOKEN.sub(r"\1[REDACTED]", record.msg)
        return True


def install_sensitive_log_filters() -> None:
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, RedactOAuthCallbackFilter) for item in access_logger.filters):
        access_logger.addFilter(RedactOAuthCallbackFilter())
    for name in ("uvicorn.access", "uvicorn.error"):
        target = logging.getLogger(name)
        if not any(isinstance(item, RedactRiderStreamFilter) for item in target.filters):
            target.addFilter(RedactRiderStreamFilter())
