"""Persistence for push subscriptions (encrypted), preferences and sent events."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import (
    WebPushEventRecord,
    WebPushPreferenceRecord,
    WebPushSubscriptionRecord,
)
from app.database.upsert import insert_if_absent
from app.notifications.models import PUSH_KINDS, PushKind, SubscriptionIn

# The backend POSTs to the endpoint a browser hands it, so only the browser
# vendors' push services are accepted (no request to an arbitrary host).
ALLOWED_PUSH_HOST_SUFFIXES: tuple[str, ...] = (
    "fcm.googleapis.com",  # Chrome, Edge (Chromium), Android
    "android.googleapis.com",
    "push.services.mozilla.com",  # Firefox
    "push.apple.com",  # Safari (macOS, installed iOS web apps)
    "notify.windows.com",  # legacy Edge
)


class InvalidSubscription(ValueError):
    pass


def validate_endpoint(endpoint: str) -> str:
    parts = urlsplit(endpoint.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.username or parts.password or parts.port not in {None, 443}:
        raise InvalidSubscription("This browser's push service is not supported.")
    if not any(host == suffix or host.endswith("." + suffix) for suffix in ALLOWED_PUSH_HOST_SUFFIXES):
        raise InvalidSubscription("This browser's push service is not supported.")
    return endpoint.strip()


def endpoint_hash(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StoredSubscription:
    id: str
    endpoint: str
    p256dh: str
    auth: str

    def info(self) -> dict[str, object]:
        return {"endpoint": self.endpoint, "keys": {"p256dh": self.p256dh, "auth": self.auth}}


@dataclass(frozen=True)
class Preferences:
    disabled: frozenset[PushKind]
    order_prompt_answered: bool

    def allows(self, kind: str) -> bool:
        return kind == "test" or kind not in self.disabled


class PushStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], encryption_key: str) -> None:
        self.sessions = sessions
        self._fernet = Fernet(encryption_key.strip().encode("ascii"))

    def _encrypt(self, subscription: SubscriptionIn) -> str:
        plain = json.dumps(
            {
                "endpoint": subscription.endpoint,
                "p256dh": subscription.keys.p256dh,
                "auth": subscription.keys.auth,
            }
        )
        return self._fernet.encrypt(plain.encode("utf-8")).decode("ascii")

    def _decrypt(self, record: WebPushSubscriptionRecord) -> StoredSubscription | None:
        try:
            data = json.loads(self._fernet.decrypt(record.encrypted_subscription.encode("ascii")))
        except (InvalidToken, ValueError):
            return None
        return StoredSubscription(str(record.id), data["endpoint"], data["p256dh"], data["auth"])

    async def save_subscription(self, user_id: str, subscription: SubscriptionIn) -> str:
        endpoint = validate_endpoint(subscription.endpoint)
        subscription = subscription.model_copy(update={"endpoint": endpoint})
        key = endpoint_hash(endpoint)
        ciphertext = self._encrypt(subscription)
        async with self.sessions() as session, session.begin():
            await insert_if_absent(
                session,
                WebPushSubscriptionRecord,
                {"user_id": user_id, "endpoint_hash": key, "encrypted_subscription": ciphertext, "failure_count": 0},
                ["endpoint_hash"],
            )
            record = (
                await session.execute(
                    select(WebPushSubscriptionRecord)
                    .where(WebPushSubscriptionRecord.endpoint_hash == key)
                    .with_for_update()
                )
            ).scalar_one()
            # Same browser, new keys or a different signed-in user: this user owns it now.
            record.user_id = user_id
            record.encrypted_subscription = ciphertext
            record.failure_count = 0
            return str(record.id)

    async def delete_subscription(self, user_id: str, endpoint: str) -> bool:
        async with self.sessions() as session, session.begin():
            result = await session.execute(
                delete(WebPushSubscriptionRecord).where(
                    WebPushSubscriptionRecord.user_id == user_id,
                    WebPushSubscriptionRecord.endpoint_hash == endpoint_hash(endpoint.strip()),
                )
            )
            return bool(result.rowcount)

    async def remove_by_id(self, subscription_id: str) -> None:
        async with self.sessions() as session, session.begin():
            await session.execute(
                delete(WebPushSubscriptionRecord).where(
                    WebPushSubscriptionRecord.id == uuid.UUID(subscription_id)
                )
            )

    async def record_result(self, subscription_id: str, *, ok: bool, at: datetime) -> None:
        values = (
            {"failure_count": 0, "last_success_at": at}
            if ok
            else {"failure_count": WebPushSubscriptionRecord.failure_count + 1}
        )
        async with self.sessions() as session, session.begin():
            await session.execute(
                update(WebPushSubscriptionRecord)
                .where(WebPushSubscriptionRecord.id == uuid.UUID(subscription_id))
                .values(**values)
            )

    async def subscriptions(self, user_id: str) -> list[StoredSubscription]:
        async with self.sessions() as session:
            records = (
                await session.execute(
                    select(WebPushSubscriptionRecord).where(WebPushSubscriptionRecord.user_id == user_id)
                )
            ).scalars()
            return [item for item in (self._decrypt(record) for record in records) if item is not None]

    async def device_count(self, user_id: str) -> int:
        async with self.sessions() as session:
            return int(
                (
                    await session.execute(
                        select(func.count()).where(WebPushSubscriptionRecord.user_id == user_id)
                    )
                ).scalar_one()
            )

    async def preferences(self, user_id: str) -> Preferences:
        async with self.sessions() as session:
            record = (
                await session.execute(
                    select(WebPushPreferenceRecord).where(WebPushPreferenceRecord.user_id == user_id)
                )
            ).scalar_one_or_none()
        if record is None:
            return Preferences(frozenset(), False)
        disabled = frozenset(kind for kind in record.disabled_kinds or [] if kind in PUSH_KINDS)
        return Preferences(disabled, record.order_prompt_answered_at is not None)  # type: ignore[arg-type]

    async def update_preferences(
        self,
        user_id: str,
        *,
        kinds: dict[PushKind, bool] | None,
        order_prompt_answered: bool | None,
        at: datetime,
    ) -> Preferences:
        async with self.sessions() as session, session.begin():
            await insert_if_absent(
                session,
                WebPushPreferenceRecord,
                {"user_id": user_id, "disabled_kinds": []},
                ["user_id"],
            )
            record = (
                await session.execute(
                    select(WebPushPreferenceRecord)
                    .where(WebPushPreferenceRecord.user_id == user_id)
                    .with_for_update()
                )
            ).scalar_one()
            if kinds:
                disabled = set(record.disabled_kinds or [])
                for kind, enabled in kinds.items():
                    (disabled.discard if enabled else disabled.add)(kind)
                record.disabled_kinds = sorted(kind for kind in disabled if kind in PUSH_KINDS)
            if order_prompt_answered is not None:
                record.order_prompt_answered_at = at if order_prompt_answered else None
            disabled_now = frozenset(record.disabled_kinds)
            answered = record.order_prompt_answered_at is not None
        return Preferences(disabled_now, answered)  # type: ignore[arg-type]

    async def claim_event(self, user_id: str, dedupe_key: str, kind: str, at: datetime) -> str | None:
        """Record the event once; None when this (user, key) was already claimed."""

        claim = uuid.uuid4()
        async with self.sessions() as session, session.begin():
            await insert_if_absent(
                session,
                WebPushEventRecord,
                {
                    "id": claim,
                    "user_id": user_id,
                    "dedupe_key": dedupe_key,
                    "kind": kind,
                    "outcome": "pending",
                    "created_at": at,
                },
                ["user_id", "dedupe_key"],
            )
            winner = (
                await session.execute(
                    select(WebPushEventRecord.id).where(
                        WebPushEventRecord.user_id == user_id,
                        WebPushEventRecord.dedupe_key == dedupe_key,
                    )
                )
            ).scalar_one()
        return str(claim) if winner == claim else None

    async def finish_event(self, event_id: str, outcome: str) -> None:
        async with self.sessions() as session, session.begin():
            await session.execute(
                update(WebPushEventRecord)
                .where(WebPushEventRecord.id == uuid.UUID(event_id))
                .values(outcome=outcome)
            )

    async def count_sent(self, user_id: str, *, since: datetime, test: bool) -> int:
        """Events that reached (or are reaching) the push service since ``since``."""

        condition = WebPushEventRecord.kind == "test" if test else WebPushEventRecord.kind != "test"
        async with self.sessions() as session:
            return int(
                (
                    await session.execute(
                        select(func.count()).where(
                            WebPushEventRecord.user_id == user_id,
                            WebPushEventRecord.created_at >= since,
                            WebPushEventRecord.outcome.in_(("pending", "sent")),
                            condition,
                        )
                    )
                ).scalar_one()
            )

    async def prune_events(self, before: datetime) -> None:
        async with self.sessions() as session, session.begin():
            await session.execute(delete(WebPushEventRecord).where(WebPushEventRecord.created_at < before))

