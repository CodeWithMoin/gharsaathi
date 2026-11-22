"""Delivering one encrypted push message to one browser push service.

Library: ``pywebpush`` (web-push-libs, maintained alongside ``py-vapid`` and
``http_ece``). It implements RFC 8291 message encryption (aes128gcm) and
RFC 8292 VAPID signing on top of ``cryptography``, which the backend already
uses. Writing that encryption by hand would be more code to get wrong. Its send
call is synchronous (``requests``), so it runs in a worker thread with a timeout.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class VapidConfig:
    public_key: str
    private_key: str
    subject: str


@dataclass(frozen=True)
class SendResult:
    """``status`` is the push service's HTTP status, or 0 when it was unreachable."""

    status: int

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def gone(self) -> bool:
        # The subscription expired or was revoked: delete it.
        return self.status in {404, 410}


class PushSender(Protocol):
    async def send(self, subscription: dict[str, object], data: str, *, ttl: int, urgency: str) -> SendResult: ...


class WebPushSender:
    def __init__(self, vapid: VapidConfig, *, timeout: float = 10.0) -> None:
        from py_vapid import Vapid02

        self._vapid = Vapid02.from_string(vapid.private_key)
        self._subject = vapid.subject
        self._timeout = timeout

    async def send(self, subscription: dict[str, object], data: str, *, ttl: int, urgency: str) -> SendResult:
        return await asyncio.to_thread(self._send, subscription, data, ttl, urgency)

    def _send(self, subscription: dict[str, object], data: str, ttl: int, urgency: str) -> SendResult:
        from pywebpush import WebPushException, webpush

        try:
            response = webpush(
                subscription,  # type: ignore[arg-type]
                data=data,
                vapid_private_key=self._vapid,
                vapid_claims={"sub": self._subject},
                ttl=ttl,
                timeout=self._timeout,
                headers={"Urgency": urgency},
            )
        except WebPushException as exc:
            # The exception text can include the endpoint; only the status leaves here.
            response = getattr(exc, "response", None)
            return SendResult(int(getattr(response, "status_code", 0) or 0))
        except Exception:  # noqa: BLE001 - network failure: treat as unreachable
            return SendResult(0)
        return SendResult(int(getattr(response, "status_code", 201)))
