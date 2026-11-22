"""``gs`` against a throwaway SQLite database: parsing and every subcommand."""

from __future__ import annotations

import io
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.database.base import Base
from app.database.models import (
    Conversation,
    MemoryItem,
    MemorySensitivity,
    MemorySourceType,
    MemoryStatus,
    MemorySubject,
    ProviderAddressState,
    ShoppingApprovalRecord,
    ShoppingBasketRecord,
    SubjectType,
    WebPushEventRecord,
    ZeptoCheckoutAttemptRecord,
)
from app.devtools import cli
from app.devtools.cli import Context, build_parser
from app.devtools.doctor import Check
from app.devtools.trace import TraceWriter

USER = "user_dev_owner"
NOW = datetime(2026, 11, 22, 9, tzinfo=UTC)
MEMORY_ID = uuid.UUID("1234abcd-0000-4000-8000-000000000001")
CONVO_ID = uuid.UUID("abcd1234-0000-4000-8000-000000000002")


@pytest_asyncio.fixture
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gs.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        person = MemorySubject(subject_type=SubjectType.PERSON, external_id=USER)
        session.add(person)
        await session.flush()
        session.add(
            MemoryItem(
                id=MEMORY_ID,
                subject_id=person.id,
                memory_type="brand_preference",
                value={"kind": "prefer_brand", "brand": "Amul", "origin": "stated"},
                text="Prefers Amul for milk",
                source_type=MemorySourceType.EXPLICIT_STATEMENT,
                status=MemoryStatus.CONFIRMED,
                sensitivity=MemorySensitivity.LOW,
                allowed_purposes=["personalize_search"],
                valid_from=NOW,
            )
        )
        session.add(ProviderAddressState(subject_id=person.id, provider="zepto", snapshot={}, selected_address_id="a1", refreshed_at=NOW))
        line = {"ref": {"provider": "zepto", "product_id": "p1", "variant_id": "v1"}, "name": "Amul Lassi", "pack_size": "200 ml", "unit_price_paise": 2500, "quantity": 2, "available": True}
        session.add(ShoppingBasketRecord(id="bz", household_id=USER, provider="zepto", address_id="a1", status="draft", revision=3, lines=[line], created_at=NOW, updated_at=NOW))
        session.add(ShoppingBasketRecord(id="bs", household_id=USER, provider="swiggy_instamart", status="checkout_pending", revision=1, lines=[{**line, "ref": {"provider": "swiggy_instamart", "product_id": "p2", "variant_id": "v2"}}], created_at=NOW, updated_at=NOW))
        session.add(ShoppingApprovalRecord(id="ap1", basket_id="bz", basket_revision=3, fingerprint="f", payable_paise=5500, status="consumed", created_at=NOW, expires_at=NOW))
        session.add(ZeptoCheckoutAttemptRecord(id="att00001xyz", approval_id="ap1", basket_id="bz", household_id=USER, idempotency_key_hash="h", payable_paise=5500, status="confirmed", provider_order_id="ZP123", created_at=NOW))
        session.add(
            Conversation(
                id=CONVO_ID,
                subject_id=person.id,
                state={
                    "messages": [
                        {"id": "m1", "role": "user", "content": "lassi chahiye", "modality": "voice", "created_at": NOW.isoformat()},
                        {"id": "m2", "role": "assistant", "content": "Do Amul Lassi add kar diye.", "created_at": NOW.isoformat()},
                    ],
                    "timeline": {
                        "summaries": [{"provider": "zepto", "after_message_id": "m2", "seq": 2, "stage": "updated", "item_count": 2, "item_total_paise": 5000}],
                        "orders": [{"provider": "zepto", "order_id": "ZP123", "after_message_id": "m2", "seq": 3, "payable": "₹55", "status": "delivered", "item_count": 2}],
                    },
                    "result_groups": [{"item_key": "lassi", "item_label": "Lassi", "query": "amul lassi", "products": [{}, {}], "after_message_id": "m1", "seq": 1}],
                },
            )
        )
        session.add(WebPushEventRecord(user_id=USER, dedupe_key="order_placed:zepto:ZP123", kind="order", outcome="sent", created_at=NOW))
        await session.commit()
    yield sessions
    await engine.dispose()


def context(tmp_path: Path, factory, **overrides) -> Context:
    settings = Settings(_env_file=None, tracking_data_path=tmp_path / "tracking", dev_trace_dir=tmp_path / "traces")
    return Context(
        settings=settings,
        out=io.StringIO(),
        err=io.StringIO(),
        session_factory_provider=lambda: factory,
        run_dir=tmp_path / ".run",
        trace_dir=tmp_path / "traces",
        **overrides,
    )


async def gs(ctx: Context, *argv: str) -> tuple[int, str, str]:
    code = await cli.run(argv, ctx)
    return code, ctx.out.getvalue(), ctx.err.getvalue()


def test_parser_covers_every_subcommand() -> None:
    parser = build_parser()
    assert parser.parse_args(["memory", "list", "--all"]).all
    assert parser.parse_args(["basket", "clear", "--provider", "zepto", "--yes"]).yes
    assert parser.parse_args(["orders", "status", "ZP1"]).code == "ZP1"
    assert parser.parse_args(["eval", "all", "--quick"]).quick
    assert parser.parse_args(["doctor", "--offline"]).offline
    assert parser.parse_args(["trace", "show", "abc"]).turn == "abc"
    with pytest.raises(SystemExit):
        parser.parse_args(["basket", "clear", "--provider", "amazon"])
    with pytest.raises(SystemExit):
        parser.parse_args(["memory"])


async def test_memory_list_show_export_delete(tmp_path: Path, factory) -> None:
    code, out, _ = await gs(context(tmp_path, factory), "memory", "list")
    assert code == 0 and "1234abcd" in out and "Prefers Amul" in out
    code, out, _ = await gs(context(tmp_path, factory), "memory", "show", "1234abcd")
    assert json.loads(out)["value"]["brand"] == "Amul"
    export = tmp_path / "export.json"
    code, _, err = await gs(context(tmp_path, factory), "memory", "export", "--out", str(export))
    assert code == 0 and json.loads(export.read_text())["items"][0]["memory_type"] == "brand_preference"
    assert export.stat().st_mode & 0o777 == 0o600

    refused = context(tmp_path, factory, confirm=lambda prompt: False)
    assert (await gs(refused, "memory", "delete", "1234abcd"))[0] == 1
    code, out, _ = await gs(context(tmp_path, factory), "memory", "delete", "1234abcd", "--yes")
    assert code == 0 and "status deleted" in out
    async with factory() as session:
        item = (await session.execute(select(MemoryItem))).scalar_one()
        assert item.status == MemoryStatus.DELETED and item.deleted_at is not None
    code, out, _ = await gs(context(tmp_path, factory), "memory", "list", "--json")
    assert json.loads(out) == []


async def test_memory_add_edit_import_are_clear_todo_hooks(tmp_path: Path, factory) -> None:
    for verb in ("add", "edit", "import"):
        code, _, err = await gs(context(tmp_path, factory), "memory", verb, "anything")
        assert code == cli.EXIT_TODO and "codex/memory-setup" in err


async def test_the_one_real_clerk_user_is_the_default_next_to_local_test_identities(tmp_path: Path, factory) -> None:
    async with factory() as session:
        session.add(MemorySubject(subject_type=SubjectType.PERSON, external_id="local-test-user"))
        await session.commit()
    code, out, _ = await gs(context(tmp_path, factory), "memory", "list")
    assert code == 0 and f"memory for {USER}" in out
    async with factory() as session:
        session.add(MemorySubject(subject_type=SubjectType.PERSON, external_id="user_second"))
        await session.commit()
    code, _, err = await gs(context(tmp_path, factory), "memory", "list")
    assert code == 1 and "Pass --user-id" in err


async def test_unknown_user_and_ambiguous_ids_explain_themselves(tmp_path: Path, factory) -> None:
    code, _, err = await gs(context(tmp_path, factory), "memory", "list", "--user-id", "user_nobody")
    assert code == 1 and "no records" in err
    code, _, err = await gs(context(tmp_path, factory), "memory", "show", "12")
    assert code == 1 and "4 characters" in err


async def test_basket_show_and_clear_never_touches_a_locked_basket(tmp_path: Path, factory) -> None:
    code, out, _ = await gs(context(tmp_path, factory), "basket", "show")
    assert "zepto · draft · rev 3 · 2 items · ₹50" in out and "LOCKED" in out
    code, out, err = await gs(context(tmp_path, factory), "basket", "clear", "--yes")
    assert code == 1
    assert "zepto: cleared 1 lines" in out and "swiggy_instamart: This basket is locked" in err
    async with factory() as session:
        rows = {r.id: r for r in (await session.execute(select(ShoppingBasketRecord))).scalars()}
    assert rows["bz"].lines == [] and rows["bz"].revision == 4
    assert len(rows["bs"].lines) == 1


async def test_orders_recent_and_status(tmp_path: Path, factory) -> None:
    code, out, _ = await gs(context(tmp_path, factory), "orders", "recent")
    assert "att00001" in out and "₹55" in out and "ZP123" in out
    code, out, _ = await gs(context(tmp_path, factory), "orders", "status", "ZP123")
    assert json.loads(out)["attempt"]["status"] == "confirmed"


async def test_reconcile_says_it_calls_zepto_and_asks_first(tmp_path: Path, factory) -> None:
    ran: list[bool] = []

    async def fake(out) -> None:
        ran.append(True)

    ctx = context(tmp_path, factory, reconcile=fake, confirm=lambda prompt: False)
    code, _, err = await gs(ctx, "orders", "reconcile", "--dry-run")
    assert code == 1 and "reads your Zepto order history" in err and "at most 5 calls" in err and not ran
    code, _, _ = await gs(context(tmp_path, factory, reconcile=fake), "orders", "reconcile", "--dry-run", "--yes")
    assert code == 0 and ran == [True]
    code, _, err = await gs(context(tmp_path, factory, reconcile=fake), "orders", "reconcile")
    assert code == cli.EXIT_USAGE and "--apply" in err


async def test_reconcile_refuses_under_a_recorded_cooldown(tmp_path: Path) -> None:
    from cryptography.fernet import Fernet

    from app.devtools.budget import ProviderCooldown
    from app.devtools.reconcile import reconcile_dry_run

    key = Fernet.generate_key().decode()
    store = tmp_path / "zepto.enc"
    until = (datetime.now(UTC) + timedelta(minutes=2)).isoformat()
    store.write_bytes(Fernet(key.encode()).encrypt(json.dumps({"client_id": "c", "rate_limited_until": until}).encode()))
    settings = Settings(_env_file=None, app_encryption_key=key, zepto_token_store_path=store)

    class MustNotRun:
        def __getitem__(self, name):
            raise AssertionError("reconcile dependencies touched under a cooldown")

    with pytest.raises(ProviderCooldown):
        await reconcile_dry_run(settings, io.StringIO(), deps=MustNotRun())  # type: ignore[arg-type]


async def test_convo_list_last_and_show_place_cards_after_messages(tmp_path: Path, factory) -> None:
    code, out, _ = await gs(context(tmp_path, factory), "convo", "list")
    assert "abcd1234" in out and "lassi chahiye" in out
    code, out, _ = await gs(context(tmp_path, factory), "convo", "last")
    lines = out.splitlines()
    assert any("you: lassi chahiye" in line for line in lines)
    results = next(i for i, line in enumerate(lines) if "results · Lassi" in line)
    reply = next(i for i, line in enumerate(lines) if "her: Do Amul" in line)
    assert results < reply
    assert "order · zepto #ZP123 · delivered · 2 items · ₹55" in out
    assert "basket · zepto · updated · 2 items · ₹50" in out
    code, out, _ = await gs(context(tmp_path, factory), "convo", "show", "abcd1234", "--json")
    assert json.loads(out)["entries"][1]["cards"][0]["card"] == "basket"


async def test_trace_last_list_show(tmp_path: Path, factory) -> None:
    ctx = context(tmp_path, factory)
    code, _, err = await gs(ctx, "trace", "last")
    assert code == 1 and "DEV_TRACE is off" in err
    writer = TraceWriter(tmp_path / "traces")
    for turn in ("aaa111", "bbb222"):
        writer.write(
            {
                "turn_id": turn, "at": "2026-11-22T10:00:00+00:00", "channel": "typed", "model": "m", "prompt_version": "1",
                "outcome": "ok", "total_ms": 4200.0,
                "rounds": [{"round": 1, "model_ms": 3000.0, "tokens": {"input": 900}, "tools": [{"name": "search_products", "args": {"query": "atta"}, "status": "ok", "ms": 800.0, "counts": {"options": 3}}]}],
                "totals": {"rounds": 1, "tools": 1, "model_ms": 3000.0, "tool_ms": 800.0},
            }
        )
    code, out, _ = await gs(context(tmp_path, factory), "trace", "last")
    assert code == 0 and "turn bbb222" in out and 'search_products(query="atta")  ok  800 ms  options=3' in out
    code, out, _ = await gs(context(tmp_path, factory), "trace", "list")
    assert "aaa111" in out and "bbb222" in out
    code, out, _ = await gs(context(tmp_path, factory), "trace", "show", "aaa")
    assert "turn aaa111" in out


async def test_eval_run_all_quick_history_and_diff(tmp_path: Path, factory) -> None:
    calls: list[list[str]] = []
    score = {"passed": 3}

    def runner(argv: list[str], cwd: Path) -> tuple[int, str, str]:
        calls.append(argv)
        return 0, json.dumps({"passed": score["passed"], "total": 3}), ""

    ctx = context(tmp_path, factory, eval_runner=runner)
    code, out, _ = await gs(ctx, "eval", "all", "--quick")
    assert code == 0 and "relevance" in out
    assert all(argv[2].split(".")[1] in {"run_relevance_benchmark", "run_recommender", "run_instamart_workflows", "run_turn_latency", "run_voice_diversity"} for argv in calls)
    # Reports go to .run/evals/reports, never the tracked evaluation/*_report.json.
    reports = [argv[argv.index("--report") + 1] for argv in calls if "--report" in argv]
    assert reports and all(str(tmp_path / ".run" / "evals" / "reports") in path for path in reports)

    score["passed"] = 2
    code, out, _ = await gs(context(tmp_path, factory, eval_runner=runner), "eval", "run", "relevance")
    assert code == 1 and "2/3" in out
    history = (tmp_path / ".run" / "evals" / "history.jsonl").read_text().splitlines()
    assert len(history) == 2
    code, out, _ = await gs(context(tmp_path, factory, eval_runner=runner), "eval", "diff")
    assert code == 1 and "worse" in out and "missing" in out
    code, _, err = await gs(context(tmp_path, factory), "eval", "run", "nope")
    assert code == cli.EXIT_USAGE


async def test_doctor_and_demo_check(tmp_path: Path, factory) -> None:
    async def red() -> list[Check]:
        return [Check("Database", "red", "down", "brew services start postgresql@17")]

    code, out, _ = await gs(context(tmp_path, factory, doctor=red), "doctor")
    assert code == 1 and "fix: brew services start postgresql@17" in out

    async def green() -> list[Check]:
        return [Check("Backend :8000", "green", "ok")]

    def runner(argv: list[str], cwd: Path) -> tuple[int, str, str]:
        return 0, json.dumps({"passed": 1, "total": 1}), ""

    code, out, _ = await gs(context(tmp_path, factory, doctor=green, eval_runner=runner), "demo-check")
    # The seeded Instamart basket is locked, so the demo is not ready.
    assert code == 1 and "locked basket: swiggy_instamart" in out and "confirmed memories" in out
    assert "offline" in out
