"""``gs``: the local operator CLI (docs/DEV_TOOLS.md).

    scripts/gs doctor
    scripts/gs memory list|show|add|edit|delete|export|import
    scripts/gs basket show|clear [--provider zepto]
    scripts/gs orders recent|status <code>|reconcile --dry-run
    scripts/gs convo list|show <id>|last
    scripts/gs trace last|list|show <turn>
    scripts/gs eval list|run <name>|all [--quick]|diff

Runs through ``scripts/backend-cli.sh``, so it uses the same database as the
local app. Only ``orders reconcile --dry-run`` can reach a store (Zepto order
history, read-only): it says so, asks first (``--yes`` skips), is refused while a
cooldown is recorded and is capped by ``ProviderCallBudget`` (≤5 calls, ≥1 s apart).
Every other command reads local data only. Output goes to this terminal only; nothing is sent or logged
elsewhere. Writes are limited to the owner's own memory (delete), basket
(clear) and the local ``.run/`` history; every one asks first (or ``--yes``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from app.core.config import REPOSITORY_ROOT, Settings, get_settings
from app.devtools import evals as evals_module
from app.devtools.budget import ProviderBudgetExceeded, ProviderCallBudget
from app.devtools.data import PROVIDERS, DevData, DevDataError
from app.devtools.trace import read_traces

VERSION = "gs 1.0"
EXIT_USAGE = 2
EXIT_TODO = 3
MEMORY_SETUP_TODO = (
    "`gs memory {verb}` is not a command yet (codex/memory-setup ships import and synth). "
    "Add or change one preference in the app (Settings → What GharSaathi knows, or say "
    "\"yaad rakhna …\"), or load a whole answers file with `gs memory import --file <path>`."
)


@dataclass
class Context:
    """Everything a command touches, so tests can swap each part."""

    settings: Settings
    out: TextIO = field(default_factory=lambda: sys.stdout)
    err: TextIO = field(default_factory=lambda: sys.stderr)
    session_factory_provider: Callable[[], Any] | None = None
    trace_dir: Path | None = None
    run_dir: Path = REPOSITORY_ROOT / ".run"
    eval_runner: evals_module.Runner = evals_module.run_process
    doctor: Callable[[], Any] | None = None
    reconcile: Callable[[TextIO], Any] | None = None
    confirm: Callable[[str], bool] | None = None
    color: bool = False
    owns_engine: bool = False

    def data(self) -> DevData:
        if self.session_factory_provider is None:
            from app.database.session import get_session_factory

            self.session_factory_provider = get_session_factory
            self.owns_engine = True
        return DevData(self.session_factory_provider(), self.settings.tracking_data_path)

    @property
    def traces(self) -> Path:
        """Traces of the running app's checkout (it writes them), else this one's."""

        if self.trace_dir is not None:
            return self.trace_dir
        from app.devtools.doctor import running_app_root

        root = running_app_root()
        path = self.settings.dev_trace_dir
        if root is not None:
            try:
                return root / path.relative_to(REPOSITORY_ROOT)
            except ValueError:
                return path
        return path

    @property
    def history(self) -> Path:
        return self.run_dir / "evals" / "history.jsonl"


# Output ------------------------------------------------------------------------
def table(rows: Sequence[dict[str, Any]], columns: Sequence[str], out: TextIO) -> None:
    if not rows:
        print("  (none)", file=out)
        return
    cells = [[_cell(row.get(column)) for column in columns] for row in rows]
    widths = [max(len(column), *(len(line[i]) for line in cells)) for i, column in enumerate(columns)]
    print("  " + "  ".join(column.ljust(widths[i]) for i, column in enumerate(columns)), file=out)
    print("  " + "  ".join("─" * width for width in widths), file=out)
    for line in cells:
        print("  " + "  ".join(value.ljust(widths[i]) for i, value in enumerate(line)), file=out)


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    text = str(value)
    return text if len(text) <= 60 else text[:59] + "…"


def dump(value: Any, out: TextIO) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str), file=out)


def _ms(value: Any) -> str:
    return f"{float(value):,.0f} ms" if isinstance(value, int | float) else "-"


def render_trace(record: dict[str, Any], out: TextIO) -> None:
    print(
        f"turn {record.get('turn_id')} · {record.get('channel')} · {record.get('at')} · "
        f"{record.get('model')} · prompt {record.get('prompt_version')} · {record.get('outcome')} · "
        f"{_ms(record.get('total_ms'))}",
        file=out,
    )
    if record.get("conversation_id") or record.get("session_id"):
        print(f"  conversation {record.get('conversation_id') or '-'} · session {record.get('session_id') or '-'}", file=out)
    for entry in record.get("rounds", []):
        tokens = entry.get("tokens") or {}
        token_text = ""
        if tokens:
            token_text = "  tokens " + " ".join(f"{key}={value:,}" for key, value in tokens.items())
        print(f"  round {entry.get('round')}  model {_ms(entry.get('model_ms'))}{token_text}", file=out)
        for tool in entry.get("tools", []):
            args = ", ".join(f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in (tool.get("args") or {}).items())
            extras = " ".join(f"{key}={value}" for key, value in (tool.get("counts") or {}).items())
            stages = " ".join(f"{key}:{value:.0f}" for key, value in (tool.get("stages") or {}).items())
            line = f"    {tool.get('name')}({args})  {tool.get('status')}  {_ms(tool.get('ms'))}"
            if extras:
                line += f"  {extras}"
            if stages:
                line += f"  [{stages}]"
            print(line, file=out)
    totals = record.get("totals") or {}
    token_total = totals.get("tokens") or {}
    print(
        f"  totals: {totals.get('rounds', 0)} rounds · {totals.get('tools', 0)} tools · model "
        f"{_ms(totals.get('model_ms'))} · tools {_ms(totals.get('tool_ms'))} · total {_ms(record.get('total_ms'))}"
        + (f" · tokens in {token_total.get('input', 0):,} out {token_total.get('output', 0):,}" if token_total else ""),
        file=out,
    )


def render_transcript(convo: dict[str, Any], out: TextIO) -> None:
    print(f"conversation {convo['id']} · {'open' if convo['active'] else 'archived'} · {convo['messages']} messages · updated {convo['updated_at']}", file=out)
    for entry in convo["entries"]:
        mark = "you" if entry["role"] == "user" else "her"
        modality = "🎙" if entry["modality"] == "voice" else "⌨"
        photo = " 📷" if entry["photo"] else ""
        print(f"  {modality} {mark:>3}: {entry['text']}{photo}", file=out)
        for card in entry["cards"]:
            print(f"        ▸ {_card(card)}", file=out)
    for card in convo.get("unplaced_cards", []):
        print(f"        ▸ {_card(card)} (before the first message)", file=out)


def _card(card: dict[str, Any]) -> str:
    if card["card"] == "results":
        return f"results · {card.get('item')} · query {card.get('query')!r} · {card.get('options')} options"
    if card["card"] == "order":
        return f"order · {card.get('provider')} #{card.get('order_id')} · {card.get('status')} · {card.get('items')} items · {card.get('payable')}"
    return f"basket · {card.get('provider')} · {card.get('stage')} · {card.get('items')} items · {card.get('item_total')}" + (
        f" · order {card['order_id']}" if card.get("order_id") else ""
    )


# Commands ----------------------------------------------------------------------
def _confirm(ctx: Context, args: argparse.Namespace, prompt: str) -> bool:
    if getattr(args, "yes", False):
        return True
    if ctx.confirm is not None:
        return ctx.confirm(prompt)
    if not sys.stdin.isatty():
        print("Refusing without a terminal; pass --yes to confirm.", file=ctx.err)
        return False
    return input(f"{prompt} [y/N] ").strip().lower() in {"y", "yes"}


async def cmd_doctor(ctx: Context, args: argparse.Namespace) -> int:
    from app.devtools import doctor

    if ctx.doctor is not None:
        checks = await ctx.doctor()
    else:
        checks = await doctor.run_checks(doctor.default_env(ctx.settings, offline=args.offline))
    if args.json:
        dump(doctor.as_json(checks), ctx.out)
    else:
        print(doctor.render(checks, color=ctx.color), file=ctx.out)
    return doctor.exit_code(checks)


async def cmd_memory(ctx: Context, args: argparse.Namespace) -> int:
    if args.verb in {"add", "edit"}:
        print(MEMORY_SETUP_TODO.format(verb=args.verb), file=ctx.err)
        return EXIT_TODO
    if args.verb in {"import", "synth"}:
        return await _memory_file_command(ctx, args)
    data = ctx.data()
    actor = await data.person(args.user_id)
    if args.verb == "list":
        rows = await data.memories(actor, include_deleted=args.all)
        if args.json:
            dump(rows, ctx.out)
            return 0
        print(f"memory for {actor.external_id}: {len(rows)} items", file=ctx.out)
        table([{**row, "id": row["id"][:8]} for row in rows], ["id", "status", "kind", "origin", "text", "created_at"], ctx.out)
        return 0
    if args.verb == "show":
        dump(await data.memory(actor, args.ref), ctx.out)
        return 0
    if args.verb == "delete":
        item = await data.memory(actor, args.ref)
        if not _confirm(ctx, args, f"Forget {item['id'][:8]} “{item['text'][:60]}”?"):
            return 1
        row = await data.delete_memory(actor, args.ref)
        print(f"forgot {row['id'][:8]} (status {row['status']})", file=ctx.out)
        return 0
    if args.verb == "export":
        exported = await data.export_memories(actor)
        if args.out:
            path = Path(args.out)
            path.write_text(json.dumps(exported, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.chmod(path, 0o600)
            print(f"exported {len(exported['items'])} items to {path}", file=ctx.err)
        else:
            dump(exported, ctx.out)
        return 0
    raise AssertionError(args.verb)


async def _memory_file_command(ctx: Context, args: argparse.Namespace) -> int:
    """`gs memory import` = make memory-seed, `gs memory synth` = make memory-synth (same flags)."""
    from app.memory import demo_seed, demo_synth

    rest = [part for part in args.rest if part != "--"]
    try:
        if args.verb == "import":
            parsed = demo_seed.parser().parse_args(rest)
            runner = demo_seed.run
        else:
            parsed = demo_synth.parse_args(rest)
            runner = demo_synth.run
    except SystemExit as exc:
        return int(exc.code or 0)
    if getattr(args, "user_id", None) and not parsed.user_id:
        parsed.user_id = args.user_id
    ctx.data()
    assert ctx.session_factory_provider is not None
    try:
        async with ctx.session_factory_provider()() as session:
            for line in await runner(parsed, session):
                print(line, file=ctx.out)
    except demo_seed.SeedError as exc:
        print(str(exc), file=ctx.err)
        return 1
    return 0


async def cmd_basket(ctx: Context, args: argparse.Namespace) -> int:
    data = ctx.data()
    actor = await data.person(args.user_id)
    household = actor.external_id
    if args.verb == "show":
        baskets = [b for b in await data.baskets(household) if not args.provider or b["provider"] == args.provider]
        if args.json:
            dump(baskets, ctx.out)
            return 0
        if not baskets:
            print("no open baskets", file=ctx.out)
        for basket in baskets:
            lock = " · LOCKED (checkout pending)" if basket["locked"] else ""
            print(
                f"{basket['provider']} · {basket['status']} · rev {basket['revision']} · {basket['item_count']} items · "
                f"{basket['item_total']} · address {'set' if basket['address_selected'] else 'not set'}{lock}",
                file=ctx.out,
            )
            table(basket["lines"], ["name", "pack_size", "quantity", "unit_price", "line_total"], ctx.out)
        return 0
    providers = [args.provider] if args.provider else list(PROVIDERS)
    if not _confirm(ctx, args, f"Clear the open basket for {', '.join(providers)}?"):
        return 1
    code = 0
    for provider in providers:
        try:
            result = await data.clear_basket(household, provider)
        except DevDataError as exc:
            print(f"{provider}: {exc}", file=ctx.err)
            code = 1
            continue
        if result["cleared"]:
            print(f"{provider}: cleared {result['removed_lines']} lines (rev {result['revision']})", file=ctx.out)
        else:
            print(f"{provider}: {result['reason']}", file=ctx.out)
    return code


async def cmd_orders(ctx: Context, args: argparse.Namespace) -> int:
    if args.verb == "reconcile":
        if not args.dry_run:
            print("Only --dry-run here. To settle matches: make reconcile-unknown ARGS=--apply", file=ctx.err)
            return EXIT_USAGE
        from app.devtools.reconcile import notice, reconcile_dry_run

        budget = ProviderCallBudget()
        print(notice(budget), file=ctx.err)
        if not _confirm(ctx, args, "Read Zepto order history now?"):
            return 1
        if ctx.reconcile is not None:
            await ctx.reconcile(ctx.out)
            return 0
        try:
            await reconcile_dry_run(ctx.settings, ctx.out, budget=budget)
        finally:
            from app.dependencies import close_cached_services

            await close_cached_services()
        return 0
    data = ctx.data()
    actor = await data.person(args.user_id)
    if args.verb == "recent":
        rows = await data.recent_orders(actor.external_id, args.limit)
        if args.json:
            dump(rows, ctx.out)
        else:
            table(rows, ["attempt", "status", "payment_method", "payable", "order_code", "created_at"], ctx.out)
        return 0
    dump(await data.order_status(actor.external_id, args.code), ctx.out)
    return 0


async def cmd_convo(ctx: Context, args: argparse.Namespace) -> int:
    data = ctx.data()
    actor = await data.person(args.user_id)
    if args.verb == "list":
        rows = await data.conversations(actor, args.limit)
        if args.json:
            dump(rows, ctx.out)
        else:
            table([{**row, "id": row["id"][:8]} for row in rows], ["id", "active", "messages", "title", "updated_at"], ctx.out)
        return 0
    convo = await data.conversation(actor, "last" if args.verb == "last" else args.ref)
    if args.json:
        dump(convo, ctx.out)
    else:
        render_transcript(convo, ctx.out)
    return 0


async def cmd_trace(ctx: Context, args: argparse.Namespace) -> int:
    records = read_traces(ctx.traces)
    if not records:
        hint = "" if ctx.settings.dev_trace else " DEV_TRACE is off: set DEV_TRACE=true in .env and make restart."
        print(f"No traces in {ctx.traces}.{hint}", file=ctx.err)
        return 1
    if args.verb == "list":
        rows = [
            {
                "turn": r.get("turn_id"),
                "at": r.get("at"),
                "channel": r.get("channel"),
                "outcome": r.get("outcome"),
                "rounds": (r.get("totals") or {}).get("rounds"),
                "tools": ",".join(t.get("name", "") for e in r.get("rounds", []) for t in e.get("tools", [])) or "-",
                "total": _ms(r.get("total_ms")),
            }
            for r in records[-args.limit :]
        ]
        table(rows, ["turn", "at", "channel", "outcome", "rounds", "tools", "total"], ctx.out)
        return 0
    if args.verb == "last":
        record = records[-1]
    else:
        matches = [r for r in records if str(r.get("turn_id", "")).startswith(args.turn)]
        if len(matches) != 1:
            print(f"{len(matches)} traces match {args.turn}.", file=ctx.err)
            return 1
        record = matches[0]
    if args.json:
        dump(record, ctx.out)
    else:
        render_trace(record, ctx.out)
    return 0


def _eval_table(results: Sequence[evals_module.EvalResult], out: TextIO) -> None:
    rows = [
        {
            "eval": r.name,
            "result": "skipped" if r.total is None else f"{r.passed}/{r.total}",
            "ok": r.ok,
            "median": _ms(r.median_ms) if r.median_ms is not None else "-",
            "wall": _ms(r.wall_ms),
            "note": r.note,
        }
        for r in results
    ]
    table(rows, ["eval", "result", "ok", "median", "wall", "note"], out)


async def cmd_eval(ctx: Context, args: argparse.Namespace) -> int:
    if args.verb == "list":
        rows = [
            {"name": s.name, "needs": s.needs, "quick": s.quick, "in_all": not s.manual, "what": s.description}
            for s in evals_module.EVALS
        ]
        table(rows, ["name", "needs", "quick", "in_all", "what"], ctx.out)
        return 0
    if args.verb == "diff":
        history = evals_module.load_history(ctx.history)
        if len(history) < 2:
            print(f"Need two runs in {ctx.history}; have {len(history)}.", file=ctx.err)
            return 1
        previous, latest = history[-2], history[-1]
        print(f"{previous['run_id']} ({previous['at']}) → {latest['run_id']} ({latest['at']})", file=ctx.out)
        rows = evals_module.diff_runs(previous, latest)
        for row in rows:
            row["before_ms"], row["after_ms"] = _ms(row["before_ms"]), _ms(row["after_ms"])
        table(rows, ["name", "before", "after", "before_ms", "after_ms", "change"], ctx.out)
        return 1 if any(row["change"] == "worse" for row in rows) else 0
    if args.verb == "run":
        try:
            specs = [evals_module.find(args.name)]
        except KeyError:
            print(f"Unknown eval {args.name}; see `gs eval list`.", file=ctx.err)
            return EXIT_USAGE
    else:
        specs = evals_module.selection(quick=args.quick)
    from app.devtools.doctor import run_command

    run = evals_module.EvalRun(
        run_id=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        at=datetime.now(UTC).isoformat(timespec="seconds"),
        quick=bool(getattr(args, "quick", False)),
        commit=run_command(["git", "-C", str(REPOSITORY_ROOT), "rev-parse", "--short", "HEAD"]),
    )
    print(f"running {len(specs)} eval(s): {', '.join(s.name for s in specs)}", file=ctx.err)
    for spec in specs:
        run.results.append(
            evals_module.run_eval(
                spec,
                reports_dir=ctx.run_dir / "evals" / "reports",
                runner=ctx.eval_runner,
                stream=lambda line: print(line, file=ctx.err),
            )
        )
    evals_module.append_history(ctx.history, run)
    _eval_table(run.results, ctx.out)
    passed = sum(r.passed or 0 for r in run.results)
    total = sum(r.total or 0 for r in run.results)
    print(f"\n{passed}/{total} passed · median {_ms(evals_module.median_of(run.results))} · history {ctx.history}", file=ctx.out)
    return 0 if all(r.ok for r in run.results) else 1


async def cmd_demo_check(ctx: Context, args: argparse.Namespace) -> int:
    """Pre-demo checklist: doctor + quick evals + memory count + Zepto address + no locked basket."""

    from app.devtools import doctor

    print("1/3 doctor" + ("" if args.online else " (--offline: no OpenAI/Jev/store calls)"), file=ctx.out)
    if ctx.doctor is not None:
        checks = await ctx.doctor()
    else:
        checks = await doctor.run_checks(doctor.default_env(ctx.settings, offline=not args.online))
    print(doctor.render(checks, color=ctx.color), file=ctx.out)
    failures = [f"doctor: {c.name}" for c in checks if c.status == "red"]

    print("\n2/3 quick evals (offline)", file=ctx.out)
    eval_args = argparse.Namespace(verb="all", quick=True, json=False, user_id=None)
    if await cmd_eval(ctx, eval_args) != 0:
        failures.append("quick evals")

    print("\n3/3 household state", file=ctx.out)
    data = ctx.data()
    actor = await data.person(args.user_id)
    memories = [m for m in await data.memories(actor) if m["status"] == "confirmed"]
    address = await data.zepto_address(actor)
    locked = [b["provider"] for b in await data.baskets(actor.external_id) if b["locked"]]
    rows = [
        {"check": "confirmed memories", "value": len(memories), "ok": bool(memories)},
        {
            "check": "Zepto address",
            "value": f"selected · {address['snapshot_age_min']} min old" if address["selected"] else "none",
            "ok": address["selected"],
        },
        {"check": "locked baskets", "value": ", ".join(locked) or "none", "ok": not locked},
    ]
    table(rows, ["check", "value", "ok"], ctx.out)
    if not memories:
        failures.append("no confirmed memory (make seed-sample-household for demo data)")
    if not address["selected"]:
        failures.append("no Zepto address (Settings → Delivery address)")
    if locked:
        failures.append(f"locked basket: {', '.join(locked)} (gs orders reconcile --dry-run)")
    print("\n" + ("READY for the demo" if not failures else "NOT READY:\n  - " + "\n  - ".join(failures)), file=ctx.out)
    return 0 if not failures else 1


# Parser ------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--user-id", help="Clerk user id (default: the only signed-in user)")
    common.add_argument("--json", action="store_true", help="machine-readable output")

    parser = argparse.ArgumentParser(prog="gs", description="GharSaathi local operator toolkit (docs/DEV_TOOLS.md).")
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("--no-color", action="store_true", help="never color output")
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")

    doctor_parser = commands.add_parser("doctor", parents=[common], help="read-only readiness table (exit 1 on red)")
    doctor_parser.add_argument("--offline", action="store_true", help="no network beyond this machine (skips OpenAI/Jev)")

    demo = commands.add_parser("demo-check", parents=[common], help="pre-demo checklist (offline by default)")
    demo.add_argument("--online", action="store_true", help="also call OpenAI/Jev once each (never the stores)")

    memory = commands.add_parser("memory", help="household memory").add_subparsers(dest="verb", required=True)
    memory.add_parser("list", parents=[common], help="list memories").add_argument("--all", action="store_true", help="include deleted")
    memory.add_parser("show", parents=[common], help="one memory with its events").add_argument("ref", help="id prefix (≥4 chars)")
    delete = memory.add_parser("delete", parents=[common], help="forget one memory (as Settings does)")
    delete.add_argument("ref")
    delete.add_argument("--yes", action="store_true")
    memory.add_parser("export", parents=[common], help="JSON export").add_argument("--out", help="write to this file (0600)")
    for verb in ("add", "edit"):
        hook = memory.add_parser(verb, parents=[common], help="(not yet: use the app, or memory import)")
        hook.add_argument("rest", nargs=argparse.REMAINDER)
    for verb, text in (
        ("import", "load an answers file (= make memory-seed; dry run unless --apply)"),
        ("synth", "synthetic demo order history (= make memory-synth; dry run unless --apply)"),
    ):
        # Their own flags are passed through (see run) to the seed tools' parsers.
        memory.add_parser(verb, parents=[common], help=text).set_defaults(rest=[])

    basket = commands.add_parser("basket", help="open baskets").add_subparsers(dest="verb", required=True)
    for verb, text in (("show", "show open baskets"), ("clear", "empty a basket (never a locked one)")):
        sub = basket.add_parser(verb, parents=[common], help=text)
        sub.add_argument("--provider", choices=PROVIDERS)
        if verb == "clear":
            sub.add_argument("--yes", action="store_true")

    orders = commands.add_parser("orders", help="checkout attempts and tracking").add_subparsers(dest="verb", required=True)
    orders.add_parser("recent", parents=[common], help="recent checkout attempts").add_argument("--limit", type=int, default=10)
    orders.add_parser("status", parents=[common], help="one order: attempt + tracking").add_argument("code", help="order code or attempt prefix")
    reconcile = orders.add_parser(
        "reconcile", parents=[common], help="CALLS ZEPTO (read-only, ≤5 calls): check unknown outcomes"
    )
    reconcile.add_argument("--dry-run", action="store_true", help="required; --apply lives in make reconcile-unknown")
    reconcile.add_argument("--yes", action="store_true", help="skip the provider-call confirmation")

    convo = commands.add_parser("convo", help="saved conversations").add_subparsers(dest="verb", required=True)
    convo.add_parser("list", parents=[common], help="recent conversations").add_argument("--limit", type=int, default=15)
    convo.add_parser("show", parents=[common], help="transcript with timeline cards").add_argument("ref", help="id prefix")
    convo.add_parser("last", parents=[common], help="the open (or newest) conversation")

    trace = commands.add_parser("trace", help="DEV_TRACE turn traces").add_subparsers(dest="verb", required=True)
    trace.add_parser("last", parents=[common], help="the newest turn")
    trace.add_parser("list", parents=[common], help="recent turns").add_argument("--limit", type=int, default=20)
    trace.add_parser("show", parents=[common], help="one turn").add_argument("turn", help="turn id prefix")

    evaluation = commands.add_parser("eval", help="evaluation scripts").add_subparsers(dest="verb", required=True)
    evaluation.add_parser("list", parents=[common], help="known evals")
    evaluation.add_parser("run", parents=[common], help="run one eval").add_argument("name")
    evaluation.add_parser("all", parents=[common], help="run every non-manual eval").add_argument(
        "--quick", action="store_true", help="offline evals only (no OpenAI/Jev calls)"
    )
    evaluation.add_parser("diff", parents=[common], help="compare the last two runs")
    return parser


HANDLERS = {
    "doctor": cmd_doctor,
    "demo-check": cmd_demo_check,
    "memory": cmd_memory,
    "basket": cmd_basket,
    "orders": cmd_orders,
    "convo": cmd_convo,
    "trace": cmd_trace,
    "eval": cmd_eval,
}


async def run(argv: Sequence[str], ctx: Context | None = None) -> int:
    parser = build_parser()
    args, extra = parser.parse_known_args(list(argv))
    if getattr(args, "command", None) == "memory" and getattr(args, "verb", None) in {"import", "synth"}:
        # Their own flags (--file, --apply, ...) are parsed by the seed tools.
        args.rest = [*getattr(args, "rest", []), *extra]
    elif extra:
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    if ctx is None:
        ctx = Context(settings=get_settings())
        ctx.color = sys.stdout.isatty() and not args.no_color and "NO_COLOR" not in os.environ
    for name in ("user_id", "json"):
        if not hasattr(args, name):
            setattr(args, name, None if name == "user_id" else False)
    try:
        return await HANDLERS[args.command](ctx, args)
    except (DevDataError, ProviderBudgetExceeded) as exc:
        print(str(exc), file=ctx.err)
        return 1
    finally:
        if ctx.owns_engine:
            from app.database.session import get_engine

            await get_engine().dispose()


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return asyncio.run(run(sys.argv[1:] if argv is None else argv))
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
