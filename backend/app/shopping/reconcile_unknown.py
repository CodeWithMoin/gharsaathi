"""Maintenance: settle existing ``outcome_unknown`` Zepto checkouts from order history.

    python -m app.shopping.reconcile_unknown --dry-run   # read-only; writes nothing
    python -m app.shopping.reconcile_unknown --apply     # settles single matches

Runs the same read-only reconciliation as the app (``list_order_history`` and
``get_order_detail`` only; never a create/confirm tool). ``--apply`` updates an
attempt only when exactly one order matches, in one transaction, and starts
its read-only tracking monitor. Re-running is safe: settled attempts are no
longer ``outcome_unknown``. Output is content-free: an attempt reference,
whether a match was found, and the store's order code.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable
from typing import Any, TextIO

from app.shopping.reconciliation import (
    BasketReader,
    CheckoutReconciler,
    OrderHistoryReader,
    ReconcilableAttempts,
    TrackingStarter,
)


async def run(
    *,
    apply: bool,
    attempts: ReconcilableAttempts,
    baskets: BasketReader,
    history_factory: Callable[[], OrderHistoryReader],
    tracking: TrackingStarter | None = None,
    out: TextIO = sys.stdout,
) -> dict[str, int]:
    """Check every unknown attempt once. The history reader is built only if needed."""

    pending = await attempts.unknown_attempts()
    counts = {"attempts": len(pending), "matched": 0, "applied": 0, "unresolved": 0}
    print(f"mode={'apply' if apply else 'dry-run'} unknown_attempts={len(pending)}", file=out)
    if not pending:
        return counts
    reconciler = CheckoutReconciler(
        attempts,
        baskets,
        history_factory(),
        tracking=tracking if apply else None,
    )
    for attempt in pending:
        outcome = await reconciler.reconcile(attempt.id, apply=apply)
        matched = outcome.check == "matched"
        counts["matched" if matched else "unresolved"] += 1
        if matched and apply and outcome.newly_reconciled:
            counts["applied"] += 1
        status = outcome.attempt.status.value if outcome.attempt is not None else "missing"
        print(
            f"attempt={attempt.id[:8]} match={'yes' if matched else 'no'} "
            f"check={outcome.check} order_code={outcome.order_code or '-'} "
            f"candidates={len(outcome.candidates)} status={status} "
            f"applied={'yes' if matched and apply and outcome.newly_reconciled else 'no'}",
            file=out,
        )
    return counts


def _live_dependencies() -> dict[str, Any]:
    from app.database.session import get_session_factory
    from app.dependencies import get_basket_repository, get_order_tracking_service, get_zepto_service
    from app.shopping.checkout import DatabaseCheckoutAttemptRepository

    return {
        "attempts": DatabaseCheckoutAttemptRepository(get_session_factory()),
        "baskets": get_basket_repository(),
        "history_factory": get_zepto_service,
        "tracking_factory": get_order_tracking_service,
    }


async def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="read only; change nothing")
    mode.add_argument("--apply", action="store_true", help="settle attempts with exactly one match")
    args = parser.parse_args(argv)
    deps = _live_dependencies()
    try:
        await run(
            apply=args.apply,
            attempts=deps["attempts"],
            baskets=deps["baskets"],
            history_factory=deps["history_factory"],
            tracking=deps["tracking_factory"]() if args.apply else None,
        )
    finally:
        from app.dependencies import close_cached_services

        await close_cached_services()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
