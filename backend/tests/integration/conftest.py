"""Integration fixtures: the real FastAPI app on a throwaway PostgreSQL database.

A session-scoped database named ``equal_ai_test_<random>`` is created, migrated
with Alembic (in a subprocess, so the live ``equal_ai`` URL is never used) and
dropped afterwards. Every test starts from empty tables. Commerce runs in
``COMMERCE_MODE=demo``; any provider a test needs beyond that is an explicit
fake installed through ``app.dependency_overrides``.

Set ``TEST_DATABASE_ADMIN_URL`` (a plain ``postgresql://`` DSN to a maintenance
database) to point somewhere other than ``postgresql://$USER@127.0.0.1:5432/postgres``.
Set ``REQUIRE_INTEGRATION_DB=1`` to fail instead of skip when it is unreachable.
"""

from __future__ import annotations

import asyncio
import getpass
import os
import secrets
import subprocess
import sys
from collections.abc import Callable, Coroutine, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.core.config as config_module
import app.database.session as session_module
import app.dependencies as dependencies_module
import app.main as main_module
import app.memory.dependencies as memory_dependencies_module
import app.rider.service as rider_service_module
import app.inbound_delivery as inbound_delivery_module
import app.shopping.providers as providers_module
from app.main import app

BACKEND_ROOT = Path(__file__).resolve().parents[2]
TEST_DATABASE_PREFIX = "equal_ai_test_"
CACHED_MODULES = (
    config_module,
    session_module,
    dependencies_module,
    memory_dependencies_module,
    providers_module,
    rider_service_module,
    inbound_delivery_module,
)


def _run(coroutine_factory: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
    """Run async admin SQL from sync fixtures, even if a loop is already running."""

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coroutine_factory())).result()


def _admin_dsn() -> str:
    return os.environ.get(
        "TEST_DATABASE_ADMIN_URL",
        f"postgresql://{getpass.getuser()}@127.0.0.1:5432/postgres",
    )


def _with_database(dsn: str, name: str) -> str:
    parts = urlsplit(dsn)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, ""))


def _redacted(dsn: str) -> str:
    parts = urlsplit(dsn)
    host = parts.hostname or "localhost"
    return f"{parts.scheme}://{parts.username or ''}@{host}:{parts.port or 5432}{parts.path}"


def _clear_cached_services() -> None:
    for module in CACHED_MODULES:
        for value in vars(module).values():
            if callable(getattr(value, "cache_clear", None)):
                value.cache_clear()


@pytest.fixture(scope="session")
def postgres_database() -> Iterator[str]:
    """Yield the SQLAlchemy URL of a freshly migrated, dedicated test database."""

    admin = _admin_dsn()

    async def probe() -> None:
        connection = await asyncpg.connect(admin, timeout=3)
        await connection.close()

    try:
        _run(probe)
    except Exception as exc:  # noqa: BLE001 - any connection failure means "no database"
        message = (
            f"PostgreSQL is not reachable at {_redacted(admin)} ({type(exc).__name__}). "
            "Start local Postgres or set TEST_DATABASE_ADMIN_URL."
        )
        if os.environ.get("REQUIRE_INTEGRATION_DB") == "1":
            pytest.fail(message)
        pytest.skip(message)

    name = f"{TEST_DATABASE_PREFIX}{secrets.token_hex(6)}"
    assert name.startswith(TEST_DATABASE_PREFIX) and name != "equal_ai"

    async def admin_sql(statement: str) -> None:
        connection = await asyncpg.connect(admin, timeout=5)
        try:
            await connection.execute(statement)
        finally:
            await connection.close()

    _run(lambda: admin_sql(f'CREATE DATABASE "{name}"'))
    url = _with_database(admin, name).replace("postgresql://", "postgresql+asyncpg://", 1)
    try:
        migration = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            env={**os.environ, "DATABASE_URL": url},
            capture_output=True,
            text=True,
            timeout=120,
        )
        if migration.returncode != 0:
            pytest.fail(f"Alembic migration failed:\n{migration.stderr[-2000:]}")
        yield url
    finally:
        _run(lambda: admin_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture
def clean_database(postgres_database: str) -> str:
    async def truncate() -> None:
        connection = await asyncpg.connect(
            postgres_database.replace("postgresql+asyncpg://", "postgresql://", 1)
        )
        try:
            tables = await connection.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                "AND tablename <> 'alembic_version'"
            )
            if tables:
                names = ", ".join(f'"{row["tablename"]}"' for row in tables)
                await connection.execute(f"TRUNCATE {names} RESTART IDENTITY CASCADE")
        finally:
            await connection.close()

    _run(truncate)
    return postgres_database


@pytest.fixture
def integration_settings(
    clean_database: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[config_module.Settings]:
    """Point every cached app service at the test database and safe local paths."""

    environment = {
        "DATABASE_URL": clean_database,
        "COMMERCE_MODE": "demo",
        "OPENAI_API_KEY": "",
        "JEV_API_KEY": "",
        "JEV_RERANKING_ENABLED": "false",
        "CLERK_SECRET_KEY": "",
        "APP_ENCRYPTION_KEY": "",
        "TRACKING_POLLING_ENABLED": "false",
        "SUPPORT_DATA_PATH": str(tmp_path / "support"),
        "TRACKING_DATA_PATH": str(tmp_path / "tracking"),
        "SWIGGY_TOKEN_STORE_PATH": str(tmp_path / "swiggy.enc"),
        "ZEPTO_TOKEN_STORE_PATH": str(tmp_path / "zepto.enc"),
        "LOCAL_ENCRYPTION_KEY_PATH": str(tmp_path / "key"),
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    _clear_cached_services()
    settings = config_module.get_settings()
    assert TEST_DATABASE_PREFIX in settings.database_url
    assert settings.commerce_mode == "demo"

    # NullPool: no connection outlives the event loop that opened it, so the
    # same engine works for TestClient's portal loop and pytest-asyncio loops.
    engine: AsyncEngine = create_async_engine(clean_database, poolclass=NullPool)
    monkeypatch.setattr(session_module, "get_engine", lambda: engine)
    monkeypatch.setattr(main_module, "settings", settings)
    try:
        yield settings
    finally:
        _clear_cached_services()


@pytest.fixture
def override(integration_settings: config_module.Settings) -> Iterator[Callable[[Any, Any], None]]:
    """Install ``app.dependency_overrides`` that are removed after the test."""

    installed: list[Any] = []

    def install(dependency: Any, replacement: Any) -> None:
        installed.append(dependency)
        app.dependency_overrides[dependency] = replacement

    yield install
    for dependency in installed:
        app.dependency_overrides.pop(dependency, None)


@pytest.fixture
def session_factory(integration_settings: config_module.Settings) -> async_sessionmaker:
    return session_module.get_session_factory()


@pytest.fixture
def api(integration_settings: config_module.Settings) -> Iterator[TestClient]:
    """The real app with its lifespan (tracking service on app.state)."""

    with TestClient(app) as client:
        yield client
