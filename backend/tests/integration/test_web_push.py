"""Browser notifications on PostgreSQL: the migrated tables, encryption at rest,
user scoping, dedupe and 410 cleanup through the real app. The push service is
a recording fake; nothing leaves the machine."""

from __future__ import annotations

import asyncio

import asyncpg

from app.dependencies import get_provider_encryption_key, get_push_notifier
from app.notifications.service import PushNotifier
from app.notifications.store import PushStore
from tests.integration.helpers import as_user
from tests.push_fakes import TEST_PUBLIC_KEY, RecordingSender, subscription


def _plain(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def test_migration_creates_the_push_tables(clean_database: str) -> None:
    async def columns() -> dict[str, set[str]]:
        connection = await asyncpg.connect(_plain(clean_database))
        try:
            rows = await connection.fetch(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_name LIKE 'web_push_%'"
            )
            unique = await connection.fetch(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'web_push_events'"
            )
        finally:
            await connection.close()
        found: dict[str, set[str]] = {}
        for row in rows:
            found.setdefault(row["table_name"], set()).add(row["column_name"])
        found["_indexes"] = {row["indexname"] for row in unique}
        return found

    found = asyncio.run(columns())
    assert {"user_id", "endpoint_hash", "encrypted_subscription", "failure_count"} <= found["web_push_subscriptions"]
    assert {"user_id", "disabled_kinds", "order_prompt_answered_at"} <= found["web_push_preferences"]
    assert {"user_id", "dedupe_key", "kind", "outcome", "created_at"} <= found["web_push_events"]
    assert {"uq_web_push_events_user_key", "ix_web_push_events_user_created"} <= found["_indexes"]


def test_subscribe_send_dedupe_and_cleanup_on_postgres(api, override, session_factory, clean_database) -> None:
    sender = RecordingSender()
    notifier = PushNotifier(
        PushStore(session_factory, get_provider_encryption_key()), sender, public_key=TEST_PUBLIC_KEY
    )
    override(get_push_notifier, lambda: notifier)

    assert api.post("/api/notifications/subscriptions", json=subscription(1), headers=as_user("alice")).status_code == 201
    assert api.post("/api/notifications/subscriptions", json=subscription(2), headers=as_user("alice")).status_code == 201
    assert api.post("/api/notifications/subscriptions", json=subscription(3), headers=as_user("bob")).status_code == 201

    async def raw_rows() -> list[str]:
        connection = await asyncpg.connect(_plain(clean_database))
        try:
            return [row["encrypted_subscription"] for row in await connection.fetch("SELECT * FROM web_push_subscriptions")]
        finally:
            await connection.close()

    stored = " ".join(asyncio.run(raw_rows()))
    assert "fcm.googleapis.com" not in stored and "authSecret" not in stored

    async def send_twice() -> list[str]:
        sender.statuses = [410, 201]
        first = await notifier.notify("alice", "delivered", "Delivered", "Delivered.", dedupe_key="track:w:delivered")
        second = await notifier.notify("alice", "delivered", "Delivered", "Delivered.", dedupe_key="track:w:delivered")
        return [first.outcome, second.outcome]

    assert asyncio.run(send_twice()) == ["sent", "duplicate"]
    assert {endpoint for endpoint, _ in sender.sent} == {subscription(1)["endpoint"], subscription(2)["endpoint"]}
    # The 410 subscription is gone; Bob's is untouched.
    assert api.get("/api/notifications/settings", headers=as_user("alice")).json()["devices"] == 1
    assert api.get("/api/notifications/settings", headers=as_user("bob")).json()["devices"] == 1

    test = api.post("/api/notifications/test", headers=as_user("bob"))
    assert test.status_code == 200 and sender.sent[-1][0] == subscription(3)["endpoint"]
