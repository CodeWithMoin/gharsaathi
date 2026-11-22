"""Read-only reconcile dry-run for dev tools, behind the provider call budget.

Same check as ``make reconcile-unknown ARGS=--dry-run`` (Zepto
``list_order_history`` / ``get_order_detail`` only), but: refused outright when a
Zepto cooldown is recorded, at most ``budget.max_calls`` calls spaced
``budget.min_spacing`` apart, never ``--apply``. With no unknown attempt it
makes no call at all.
"""

from __future__ import annotations

from typing import Any, TextIO

from app.core.config import Settings
from app.devtools.budget import BudgetedOrderHistory, ProviderCallBudget, refuse_if_cooling_down
from app.devtools.doctor import _encryption_key, read_connection

PROVIDER_NOTICE = (
    "This reads your Zepto order history (read-only: list_order_history, get_order_detail; "
    "at most {max_calls} calls, ≥{spacing:g} s apart; no cart or order tool). Only if an "
    "order outcome is unknown."
)


def notice(budget: ProviderCallBudget) -> str:
    return PROVIDER_NOTICE.format(max_calls=budget.max_calls, spacing=budget.min_spacing)


async def reconcile_dry_run(
    settings: Settings,
    out: TextIO,
    *,
    budget: ProviderCallBudget | None = None,
    deps: dict[str, Any] | None = None,
) -> dict[str, int]:
    connection = read_connection(settings.zepto_token_store_path, _encryption_key(settings))
    if isinstance(connection, dict):
        refuse_if_cooling_down("Zepto", connection["rate_limited_until"])
    budget = budget or ProviderCallBudget()
    if deps is None:
        from app.shopping.reconcile_unknown import _live_dependencies

        deps = _live_dependencies()
    from app.shopping.reconcile_unknown import run

    try:
        return await run(
            apply=False,
            attempts=deps["attempts"],
            baskets=deps["baskets"],
            history_factory=lambda: BudgetedOrderHistory(deps["history_factory"](), budget),
            out=out,
        )
    finally:
        print(f"zepto_calls={len(budget.calls)}/{budget.max_calls}", file=out)
