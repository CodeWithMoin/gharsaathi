"""Data-preserving migrations, run against their own throwaway PostgreSQL database."""

from __future__ import annotations

import os
import secrets
import subprocess
import sys

import asyncpg

from tests.integration.conftest import (
    BACKEND_ROOT,
    TEST_DATABASE_PREFIX,
    _admin_dsn,
    _run,
    _with_database,
)


def _alembic(url: str, target: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", target],
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]


def test_one_open_basket_migration_resolves_existing_duplicates(postgres_database: str) -> None:
    admin = _admin_dsn()
    name = f"{TEST_DATABASE_PREFIX}mig_{secrets.token_hex(4)}"
    assert name.startswith(TEST_DATABASE_PREFIX)
    plain = _with_database(admin, name)
    url = plain.replace("postgresql://", "postgresql+asyncpg://", 1)

    async def admin_sql(statement: str) -> None:
        connection = await asyncpg.connect(admin, timeout=5)
        try:
            await connection.execute(statement)
        finally:
            await connection.close()

    async def seed() -> None:
        connection = await asyncpg.connect(plain, timeout=5)
        try:
            await connection.execute(
                """
                INSERT INTO shopping_baskets
                    (id, household_id, provider, address_id, status, revision, lines, created_at, updated_at)
                VALUES
                    ('a-old',      'alice', 'zepto', NULL, 'draft',             1, '[]', now(), now() - interval '2 hours'),
                    ('a-new',      'alice', 'zepto', NULL, 'awaiting_approval', 1, '[]', now(), now() - interval '1 hour'),
                    ('a-checkout', 'alice', 'zepto', NULL, 'checkout_pending',  1, '[]', now(), now() - interval '3 hours'),
                    ('a-swiggy',   'alice', 'swiggy_instamart', NULL, 'draft',  1, '[]', now(), now()),
                    ('b-1',        'bob',   'zepto', NULL, 'draft',             1, '[]', now(), now() - interval '1 hour'),
                    ('b-2',        'bob',   'zepto', NULL, 'approved',          1, '[]', now(), now()),
                    ('b-done',     'bob',   'zepto', NULL, 'ordered',           1, '[]', now(), now())
                """
            )
            await connection.execute(
                """
                INSERT INTO shopping_approvals
                    (id, basket_id, basket_revision, fingerprint, payable_paise, status, created_at, expires_at)
                VALUES
                    ('ap-a-new', 'a-new', 1, repeat('a', 64), 100, 'pending',  now(), now() + interval '10 minutes'),
                    ('ap-b-1',   'b-1',   1, repeat('b', 64), 100, 'approved', now(), now() + interval '10 minutes'),
                    ('ap-b-2',   'b-2',   1, repeat('c', 64), 100, 'approved', now(), now() + interval '10 minutes')
                """
            )
        finally:
            await connection.close()

    async def read() -> tuple[dict[str, str], dict[str, str], str]:
        connection = await asyncpg.connect(plain, timeout=5)
        try:
            baskets = dict(await connection.fetch("SELECT id, status FROM shopping_baskets"))
            approvals = dict(await connection.fetch("SELECT id, status FROM shopping_approvals"))
            try:
                await connection.execute(
                    "INSERT INTO shopping_baskets VALUES "
                    "('a-dup', 'alice', 'zepto', NULL, 'draft', 0, '[]', now(), now())"
                )
                duplicate = "inserted"
            except asyncpg.UniqueViolationError:
                duplicate = "rejected"
            return baskets, approvals, duplicate
        finally:
            await connection.close()

    _run(lambda: admin_sql(f'CREATE DATABASE "{name}"'))
    try:
        _alembic(url, "20261122_0008")
        _run(seed)
        _alembic(url, "head")
        baskets, approvals, duplicate = _run(read)
    finally:
        _run(lambda: admin_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))

    assert baskets == {
        # An in-flight checkout is never abandoned, even when older.
        "a-checkout": "checkout_pending",
        "a-old": "abandoned",
        "a-new": "abandoned",
        "a-swiggy": "draft",
        # Otherwise the most recently updated open basket is kept.
        "b-2": "approved",
        "b-1": "abandoned",
        "b-done": "ordered",
    }
    assert approvals == {"ap-a-new": "superseded", "ap-b-1": "superseded", "ap-b-2": "approved"}
    assert duplicate == "rejected"
