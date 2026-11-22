"""Fakes for browser-notification tests: a recording push sender. No network."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from cryptography.fernet import Fernet

from app.notifications.sender import SendResult

TEST_PUBLIC_KEY = "BPublicKeyForTestsOnly"
TEST_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")


def subscription(n: int = 1, host: str = "fcm.googleapis.com") -> dict[str, object]:
    return {
        "endpoint": f"https://{host}/fcm/send/device-token-{n:04d}",
        "keys": {"p256dh": f"BKeyMaterialP256dhValue{n:04d}xyz", "auth": f"authSecret{n:04d}"},
    }


@dataclass
class RecordingSender:
    """Answers each send with the next scripted status (default 201); records payloads."""

    statuses: list[int] = field(default_factory=list)
    raises: bool = False
    sent: list[tuple[str, dict]] = field(default_factory=list)

    async def send(self, subscription: dict[str, object], data: str, *, ttl: int, urgency: str) -> SendResult:
        if self.raises:
            raise ConnectionError("push service down")
        self.sent.append((str(subscription["endpoint"]), json.loads(data)))
        return SendResult(self.statuses.pop(0) if self.statuses else 201)

    def payloads(self) -> list[dict]:
        return [payload for _, payload in self.sent]
