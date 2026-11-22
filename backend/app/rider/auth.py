"""Basic auth for the Exotel stream (``wss://user:password@host/...``).

Exotel sends the URL credentials as ``Authorization: Basic base64(user:password)``
(https://developer.exotel.com/docs/agentstream/stream-voicebot-applet).
"""

from __future__ import annotations

import base64
import binascii
import hmac

from app.core.config import Settings


def stream_auth_configured(settings: Settings) -> bool:
    return bool(settings.exotel_stream_user.strip()) and len(
        settings.exotel_stream_password.get_secret_value()
    ) >= 16


def basic_credentials(header: str | None) -> tuple[str, str] | None:
    if not header:
        return None
    scheme, _, value = header.strip().partition(" ")
    if scheme.lower() != "basic" or not value.strip():
        return None
    try:
        decoded = base64.b64decode(value.strip(), validate=True).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    user, separator, password = decoded.partition(":")
    if not separator:
        return None
    return user, password


def stream_request_authorised(settings: Settings, header: str | None) -> bool:
    """Constant-time check of both parts; refuses when no credentials are configured."""

    if not stream_auth_configured(settings):
        return False
    credentials = basic_credentials(header)
    if credentials is None:
        return False
    user, password = credentials
    user_ok = hmac.compare_digest(user.encode(), settings.exotel_stream_user.strip().encode())
    password_ok = hmac.compare_digest(
        password.encode(), settings.exotel_stream_password.get_secret_value().encode()
    )
    return user_ok and password_ok
