"""Demo-only synthetic order history, consistent with the owner's household answers.

    make memory-synth ARGS="--file .run/demo-memory.yaml --months 12 --seed 42 --dry-run"
    make memory-synth ARGS="--file .run/demo-memory.yaml --apply [--turn-on-history] [--i-understand-synthetic]"
    make memory-synth ARGS="--remove [--i-understand-synthetic]"

It writes ~N months of plausible delivered Swiggy Instamart and Zepto orders for
one user into the purchase graph only, so purchase patterns, usual suggestions,
the recommender and memory recall have realistic variety for a demo.

Rules, enforced in code:

- Everything comes from the answers file (usual buys, routines, diet, avoid list,
  preferred store, order times). Every generated product is checked against the
  household's own diet, keep-outs and avoid rules (app.memory.household_rules);
  a conflicting product is never written.
- Order ids start with ``synth-`` (``SYNTHETIC_ORDER_PREFIX``); ``--remove``
  deletes exactly those orders and the products only they referenced. No prices
  are generated (total 0), so nothing here can look like a bill.
- Synthetic orders are not provider orders: they never get a provider lookup
  id, never stop an incremental provider import or count as already known, and
  are never "the last order" (``PurchaseGraphService.last_real_order``).
  Tracking, order status, reconciliation and order help read the stores' own
  order lists, never the purchase graph. "What GharSaathi knows" labels them
  "From demo history".
- With COMMERCE_MODE=live (the owner's real database) it refuses to run unless
  ``--i-understand-synthetic`` is passed. Dry run is the default.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.commerce.swiggy.models import OrderItem, OrderList, OrderSummary, Price, Product
from app.core.config import get_settings
from app.database.models import MemorySubject, PurchaseOccurrenceEdge, PurchaseOrderNode
from app.memory.demo_seed import DemoMemoryFile, SeedError, load_file, mask, resolve_user, wanted_items
from app.memory.household_rules import HouseholdRules, Rule, product_decision
from app.memory.purchase_graph import (
    DATA_CATEGORY,
    PURPOSE,
    SYNTHETIC_ORDER_PREFIX,
    PurchaseGraphService,
)
from app.memory.remembered import pack_key
from app.memory.schemas import MemoryPermissionUpsert
from app.memory.service import MemoryService

IST = timezone(timedelta(hours=5, minutes=30))
SYNTH_RETENTION_DAYS = 400
Daypart = Literal["morning", "afternoon", "evening", "late_night"]
Provider = Literal["swiggy", "zepto"]
STORE_OF = {"swiggy_instamart": "swiggy", "zepto": "zepto"}
_HOURS: dict[str, tuple[int, int]] = {"morning": (7, 9), "afternoon": (13, 16), "evening": (18, 21), "late_night": (22, 24)}


class SynthRefused(SeedError):
    pass


@dataclass(frozen=True)
class Variant:
    name: str
    pack: str


@dataclass
class Line:
    """One thing the history buys repeatedly, with its cadence."""

    category: str
    variants: list[Variant]
    every: tuple[float, float]  # days between purchases
    dayparts: dict[str, float]
    quantity: tuple[int, int] = (1, 1)
    # Month -> multiplier on frequency (lassi in summer).
    season: dict[int, float] = field(default_factory=dict)
    # Closest alternatives used now and then when the usual one is "out of stock".
    alternates: list[Variant] = field(default_factory=list)
    alternate_rate: float = 0.0
    alternate_cycle: bool = False  # variants alternate (dark chocolates)
    # Bought with a late-night order rather than on its own cadence.
    with_late_night: float = 0.0
    # Bought on cook days only, with a few other basics.
    cook_day: bool = False


@dataclass(frozen=True)
class SynthItem:
    category: str
    name: str
    quantity: int


@dataclass(frozen=True)
class SynthOrder:
    order_id: str
    provider: Provider
    at: datetime
    daypart: str
    items: tuple[SynthItem, ...]


# Catalogue ------------------------------------------------------------------

_SUMMER = {4: 3.0, 5: 3.5, 6: 2.5, 3: 1.5, 7: 1.3}
_VEGETABLES = [
    Variant("Fresh Tomato (Hybrid)", "500 g"), Variant("Fresh Potato", "1 kg"),
    Variant("Coriander Leaves", "100 g"), Variant("Green Chilli", "100 g"),
    Variant("Fresh Ginger", "100 g"), Variant("Fresh Garlic", "100 g"),
    Variant("Green Capsicum", "250 g"), Variant("Lemon", "4 pcs"),
    Variant("Cucumber", "500 g"), Variant("Spinach (Palak)", "250 g"),
    Variant("Fresh Carrot", "500 g"), Variant("Cauliflower", "1 pc"),
]
_COOK_EXTRAS = [
    Variant("Licious Chicken Breast Boneless", "450 g"),
    Variant("Amul Fresh Paneer", "200 g"),
    Variant("Tata Sampann Toor Dal", "1 kg"),
    Variant("India Gate Basmati Rice Everyday", "1 kg"),
    Variant("Fortune Sunflower Refined Oil", "1 L"),
    Variant("Amul Butter", "100 g"),
]
ONE_OFFS = [
    ("stationery", Variant("Classmate Single Line Notebook 172 Pages", "1 pc")),
    ("stationery", Variant("Reynolds Trimax Gel Pen Blue", "2 pcs")),
    ("electronics", Variant("Ambrane USB-C to USB-C Fast Charging Cable 1 m", "1 pc")),
    ("electronics", Variant("Duracell AA Alkaline Batteries", "4 pcs")),
    ("cleaning", Variant("Surf Excel Easy Wash Detergent Powder", "1 kg")),
    ("cleaning", Variant("Vim Lemon Dishwash Gel", "500 ml")),
    ("personal care", Variant("Dettol Original Liquid Handwash Refill", "675 ml")),
    ("personal care", Variant("Colgate Strong Teeth Toothpaste", "200 g")),
    ("household", Variant("Origami Soft Facial Tissues 200 Pulls", "1 pc")),
    ("household", Variant("Odonil Room Freshener Blocks", "4 x 50 g")),
]
_NIGHT = {"evening": 0.45, "late_night": 0.5, "afternoon": 0.05}
_MORNING = {"morning": 0.85, "evening": 0.15}


def _title(text: str) -> str:
    words = []
    for word in text.split():
        words.append(word if any(char.isupper() for char in word) or not word.isalpha() else word.capitalize())
    return " ".join(words)


def _usual_name(brand: str | None, product: str | None, fallback: str) -> str:
    name = _title(" ".join(part for part in (brand, product) if part) or fallback)
    if brand and product and product.casefold().startswith(brand.casefold()):
        name = _title(product)
    return name


def _pack(size: str | None, default: str) -> tuple[str, str]:
    """(pack size, extra name words) from a told size such as '500 ml', 'can', '4-pack'."""
    text = (size or "").strip()
    if not text:
        return default, ""
    if pack_key(text) is not None:
        # "1 pack (144-150 g)" -> "150 g".
        key = pack_key(text)
        assert key is not None
        if "(" in text:
            return f"{int(key[1])} {key[2]}", ""
        return text, ""
    folded = text.casefold()
    if "can" in folded:
        return "300 ml", "Can"
    digits = "".join(char for char in folded if char.isdigit())
    if "pack" in folded and digits:
        return default, f"{digits} Pack"
    return default, ""


def _has(words: str, *names: str) -> bool:
    return any(re.search(rf"(?<![a-z]){re.escape(name)}", words) for name in names)


def _line_for(category: str, brand: str | None, product: str | None, size: str | None) -> Line:
    words = f"{category} {brand or ''} {product or ''}".casefold()
    # Most specific first: "Milky Mist Greek yogurt" is not milk, "chocolate" is not cola.
    if _has(words, "yogurt", "yoghurt", "curd", "dahi"):
        pack, _ = _pack(size, "100 g")
        return Line("dairy", [Variant(_usual_name(brand, product, "Greek Yogurt Plain"), pack)], (6.0, 8.0), {"morning": 0.4, "evening": 0.6}, (1, 3))
    if _has(words, "lassi", "chaas", "buttermilk"):
        pack, _ = _pack(size, "200 ml")
        fallback = "Amul Masti Spiced Buttermilk" if _has(words, "chaas", "buttermilk") else "Amul Sweet Lassi"
        name = _usual_name(brand, product, fallback) if brand else fallback
        return Line("dairy drinks", [Variant(name, pack)], (18.0, 30.0), {"afternoon": 0.3, "evening": 0.6, "late_night": 0.1}, (2, 4), season=_SUMMER)
    if _has(words, "oat"):
        pack, _ = _pack(size, "400 g")
        return Line("breakfast", [Variant(_usual_name(brand, product, "Oats"), pack)], (25.0, 35.0), {"morning": 0.5, "evening": 0.5})
    if _has(words, "chocolate", "cocoa"):
        pack, _ = _pack(size, "40 g")
        return Line("chocolate", [Variant(_usual_name(brand, product, "Dark Chocolate"), pack)], (6.0, 10.0), _NIGHT)
    if _has(words, "milk") and not _has(words, "milkshake"):
        pack, _ = _pack(size, "500 ml")
        main = Variant(_usual_name(brand, product, "Toned Milk"), pack)
        return Line("milk", [main], (2.0, 4.0), _MORNING, (1, 2),
                    alternates=[Variant("Nandini Toned Milk", pack), Variant("Mother Dairy Toned Milk", pack)],
                    alternate_rate=0.03)
    if _has(words, "egg"):
        return Line("eggs", [Variant(_usual_name(brand, product, "Farm Fresh White Eggs"), "12 pcs" if not size or "12" in size else _pack(size, "12 pcs")[0])],
                    (8.0, 12.0), {"morning": 0.3, "evening": 0.5, "late_night": 0.2})
    if _has(words, "maggi", "noodle"):
        pack, extra = _pack(size, "4 x 70 g")
        name = _usual_name(brand, product, "Maggi Masala Noodles")
        return Line("instant food", [Variant(f"{name} {extra}".strip(), pack)], (12.0, 16.0), {"evening": 0.35, "late_night": 0.65})
    if _has(words, "coke", "cold drink", "cola", "soft drink", "soda"):
        pack, extra = _pack(size, "300 ml")
        name = _usual_name(brand, product, "Diet Coke")
        return Line("cold drinks", [Variant(f"{name} {extra}".strip(), pack)], (1.5, 3.5), {"evening": 0.4, "late_night": 0.6}, (1, 3))
    if _has(words, "protein"):
        name = _usual_name(brand, product, "Protein Bar")
        flavours = [Variant(f"{name} Choco Almond", "60 g"), Variant(f"{name} Peanut Butter", "60 g"), Variant(f"{name} Coffee Mocha", "60 g")]
        return Line("protein", flavours, (6.0, 9.0), _NIGHT, (2, 4))
    if _has(words, "almond", "cashew", "dry fruit", "nuts", "pista", "walnut"):
        default = "Happilo Premium California Almonds" if "almond" in words else "Happilo Whole Cashews W320" if "cashew" in words else "Happilo Dry Fruits Mix"
        pack, _ = _pack(size, "200 g")
        return Line("dry fruits", [Variant(_usual_name(brand, product, default) if brand else default, pack)], (28.0, 40.0), _NIGHT)
    if _has(words, "malkist", "cracker", "snack", "chips", "namkeen"):
        pack, _ = _pack(size, "138 g")
        return Line("snacks", [Variant(_usual_name(brand, product, "Cheese Crackers"), pack)], (20.0, 30.0), {"late_night": 1.0}, with_late_night=0.45)
    if _has(words, "onion", "vegetable", "sabzi", "tomato"):
        # Bought on cook days (see generate), not on a cadence of its own.
        return Line("vegetables", [Variant("Fresh Onion", "1 kg")], (7.0, 7.0), {"evening": 1.0}, with_late_night=0.0, cook_day=True)
    pack, _ = _pack(size, "1 pc")
    return Line(category.casefold()[:30] or "other", [Variant(_usual_name(brand, product, category), pack)], (10.0, 20.0), _NIGHT)


def build_lines(answers: DemoMemoryFile) -> list[Line]:
    lines: list[Line] = []
    by_category: dict[str, Line] = {}
    for buy in answers.usual_buys:
        line = _line_for(buy.category, buy.brand, buy.product, buy.size)
        existing = by_category.get(line.category)
        if existing is not None and line.category in {"chocolate", "dry fruits", "dairy drinks"}:
            if line.category == "chocolate":
                existing.alternate_cycle = True
                existing.variants.extend(line.variants)
                continue
        by_category.setdefault(line.category, line)
        lines.append(line)
    return lines


# Household consistency -------------------------------------------------------


def household_rules(answers: DemoMemoryFile) -> HouseholdRules:
    rules = []
    for wanted in wanted_items(answers):
        spec = wanted.spec
        if spec.kind in {"diet", "avoid_ingredient", "avoid_product", "avoid_brand"}:
            rules.append(Rule(spec.kind, spec.value, spec.item))
    return HouseholdRules(rules)


def allowed(rules: HouseholdRules, name: str) -> bool:
    """Would this household's own rules show this product unasked? Hidden or flagged means no."""
    product = Product(
        product_id="synth", sku_id="synth", spin_id="synth", name=name,
        price=Price(mrp=0, offer_price=0), available=True,
    )
    decision = product_decision(rules, product, "zepto", "") or {}
    return not (decision.get("drop") or decision.get("avoided"))


# Generation ------------------------------------------------------------------


def _pick_daypart(rng: random.Random, weights: dict[str, float]) -> str:
    parts, values = zip(*weights.items(), strict=True)
    return rng.choices(parts, weights=values)[0]


def _order_times(answers: DemoMemoryFile) -> dict[str, float]:
    told = set(answers.usual_order_times)
    if not told:
        return _NIGHT
    weights = {"morning": 0.05, "afternoon": 0.05, "evening": 0.1, "late_night": 0.1}
    for part in told:
        key = "late_night" if part in {"late night", "night"} else "morning" if "morning" in part else part
        weights[key] = weights.get(key, 0) + 0.8 / len(told)
    return weights


def _consolidate(
    slots: dict[tuple[date, str], Counter[tuple[str, str]]],
    rng: random.Random,
    night_weights: dict[str, float],
    start_day: date,
    end_day: date,
) -> dict[tuple[date, str], Counter[tuple[str, str]]]:
    """Fold item-level purchases into 3-4 orders a week, the way people stock up."""
    shop_days: list[tuple[date, str]] = []
    week = start_day
    while week <= end_day:
        days = sorted(rng.sample(range(7), rng.choice((3, 3, 4))))
        for offset in days:
            day = week + timedelta(days=offset)
            if day <= end_day:
                shop_days.append((day, _pick_daypart(rng, night_weights)))
        week += timedelta(days=7)
    merged: dict[tuple[date, str], Counter[tuple[str, str]]] = defaultdict(Counter)
    if not shop_days:
        return slots
    for (day, _part), basket in slots.items():
        target = next((slot for slot in shop_days if slot[0] >= day), shop_days[-1])
        merged[target].update(basket)
    return merged


def generate(
    answers: DemoMemoryFile,
    *,
    months: int = 12,
    seed: int = 42,
    end: date | None = None,
    realistic: bool = False,
) -> list[SynthOrder]:
    """A deterministic history (same answers, months, seed and end date: same orders)."""
    if not 1 <= months <= 24:
        raise SynthRefused("--months must be 1 to 24.")
    rng = random.Random(seed)
    rules = household_rules(answers)
    lines = build_lines(answers)
    end_day = end or datetime.now(IST).date() - timedelta(days=1)
    start_day = end_day - timedelta(days=months * 30)
    night_weights = _order_times(answers)
    preferred = STORE_OF.get(answers.preferred_store or "", None)

    # Each slot (day, daypart) collects the items bought then.
    slots: dict[tuple[date, str], Counter[tuple[str, str]]] = defaultdict(Counter)
    for line in lines:
        if line.with_late_night or line.cook_day:
            continue
        day = start_day + timedelta(days=rng.uniform(0, line.every[0]))
        cycle = 0
        while day <= end_day:
            weights = night_weights if line.dayparts is _NIGHT else line.dayparts
            part = _pick_daypart(rng, weights)
            variant = line.variants[cycle % len(line.variants)] if line.alternate_cycle else rng.choice(line.variants)
            cycle += 1
            if line.alternates and rng.random() < line.alternate_rate:
                variant = rng.choice(line.alternates)
            name = f"{variant.name} {variant.pack}".strip()
            slots[(day, part)][(line.category, name)] += rng.randint(*line.quantity)
            gap = rng.uniform(*line.every) / line.season.get(day.month, 1.0)
            day += timedelta(days=max(1.0, gap))

    # Cook days: onions plus a few basics, once or twice a week.
    cook_lines = [line for line in lines if line.cook_day]
    if cook_lines:
        day = start_day
        while day <= end_day:
            for _ in range(rng.choice((1, 1, 2))):
                cook = day + timedelta(days=rng.randrange(7))
                if cook > end_day:
                    continue
                part = "evening" if rng.random() < 0.8 else "afternoon"
                for line in cook_lines:
                    variant = rng.choice(line.variants)
                    if rng.random() < 0.8:
                        slots[(cook, part)][(line.category, f"{variant.name} {variant.pack}")] += 1
                for veg in rng.sample(_VEGETABLES, rng.randint(2, 4)):
                    slots[(cook, part)][("vegetables", f"{veg.name} {veg.pack}")] += 1
                if rng.random() < 0.3:
                    extra = rng.choice(_COOK_EXTRAS)
                    slots[(cook, part)][("cooking basics", f"{extra.name} {extra.pack}")] += 1
            day += timedelta(days=7)

    # Night snacks ride along with late-night orders.
    for line in lines:
        if not line.with_late_night:
            continue
        for (day, part), basket in list(slots.items()):
            if part == "late_night" and rng.random() < line.with_late_night:
                variant = rng.choice(line.variants)
                basket[(line.category, f"{variant.name} {variant.pack}")] += 1

    # One-offs for catalogue variety: about one or two a month.
    for _ in range(int(months * 1.6)):
        category, variant = rng.choice(ONE_OFFS)
        day = start_day + timedelta(days=rng.randrange((end_day - start_day).days + 1))
        slots[(day, _pick_daypart(rng, night_weights))][(category, f"{variant.name} {variant.pack}")] += 1

    if realistic:
        slots = _consolidate(slots, rng, night_weights, start_day, end_day)

    orders: list[SynthOrder] = []
    for (day, part), basket in sorted(slots.items()):
        items = tuple(
            SynthItem(category, name, quantity)
            for (category, name), quantity in sorted(basket.items())
            if allowed(rules, name)
        )
        if not items:
            continue
        start, stop = _HOURS[part]
        minute = rng.randrange((stop - start) * 60 + (90 if part == "late_night" else 0))
        at = datetime.combine(day, datetime.min.time(), IST) + timedelta(hours=start, minutes=minute)
        if preferred and rng.random() < 0.7:
            provider: Provider = preferred  # type: ignore[assignment]
        else:
            provider = "zepto" if rng.random() < 0.55 else "swiggy"
        order_id = f"{SYNTHETIC_ORDER_PREFIX}{provider}-{day:%Y%m%d}-{part.replace('_', '')}"
        orders.append(SynthOrder(order_id, provider, at, part, items))
    return orders


def order_lists(orders: list[SynthOrder]) -> dict[str, OrderList]:
    """The orders in the shape the purchase graph imports (no prices, no lookup id)."""
    by_provider: dict[str, list[OrderSummary]] = defaultdict(list)
    for order in orders:
        by_provider[order.provider].append(
            OrderSummary(
                order_id=order.order_id,
                status="delivered",
                current_status="delivered",
                history_status="delivered",
                created_at=order.at,
                updated_at=order.at + timedelta(minutes=14),
                item_count=len(order.items),
                total_paise=0,
                order_type="ZEPTO" if order.provider == "zepto" else "INSTAMART",
                is_active=False,
                items=[OrderItem(name=item.name, quantity=item.quantity) for item in order.items],
            )
        )
    return {provider: OrderList(orders=summaries) for provider, summaries in by_provider.items()}


# Report ----------------------------------------------------------------------


def report(orders: list[SynthOrder]) -> list[str]:
    months = sorted({order.at.strftime("%Y-%m") for order in orders})
    per: dict[str, Counter[str]] = defaultdict(Counter)
    for order in orders:
        for item in order.items:
            per[item.category][order.at.strftime("%Y-%m")] += 1
    providers = Counter(order.provider for order in orders)
    parts = Counter(order.daypart for order in orders)
    items = sum(len(order.items) for order in orders)
    lines = [
        f"orders {len(orders)}, order lines {items}; "
        + ", ".join(f"{name} {count}" for name, count in sorted(providers.items()))
        + "; "
        + ", ".join(f"{name} {count}" for name, count in sorted(parts.items())),
        "lines per category per month:",
        "  " + "category".ljust(16) + " ".join(month[2:] for month in months) + "  total",
    ]
    for category in sorted(per):
        counts = per[category]
        lines.append(
            "  " + category[:15].ljust(16) + " ".join(f"{counts[month]:>5}" for month in months)
            + f"  {sum(counts.values()):>5}"
        )
    top = Counter(item.name for order in orders for item in order.items).most_common(8)
    lines.append("most frequent: " + "; ".join(f"{name} ×{count}" for name, count in top))
    return lines


# Apply / remove ----------------------------------------------------------------


async def _require_history(session: AsyncSession, actor: MemorySubject, turn_on: bool) -> None:
    graph = PurchaseGraphService(session)
    if await graph.has_history_permission(actor):
        return
    if not turn_on:
        raise SynthRefused(
            "Purchase history is off for this user. Turn it on in the app, or pass --turn-on-history."
        )
    await MemoryService(session).set_permission(
        actor, DATA_CATEGORY, PURPOSE,
        MemoryPermissionUpsert(allowed=True, granularity="demo_synthetic_history", retention_days=SYNTH_RETENTION_DAYS),
    )


async def remove_synthetic(session: AsyncSession, actor: MemorySubject, keep: set[str] | None = None) -> int:
    doomed = select(PurchaseOrderNode.id).where(
        PurchaseOrderNode.subject_id == actor.id,
        PurchaseOrderNode.provider_order_id.startswith(SYNTHETIC_ORDER_PREFIX),
    )
    if keep:
        doomed = doomed.where(PurchaseOrderNode.provider_order_id.not_in(keep))
    ids = list((await session.execute(doomed)).scalars().all())
    if not ids:
        return 0
    # Edges first: not every database enforces the cascade (SQLite in tests).
    await session.execute(
        delete(PurchaseOccurrenceEdge)
        .where(PurchaseOccurrenceEdge.subject_id == actor.id, PurchaseOccurrenceEdge.order_id.in_(ids))
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        delete(PurchaseOrderNode).where(PurchaseOrderNode.id.in_(ids)).execution_options(synchronize_session=False)
    )
    # Products only synthetic orders referenced go too (the graph's own orphan sweep).
    await PurchaseGraphService(session)._prune_expired(actor.id, datetime.now(UTC))
    await session.commit()
    return len(ids)


async def apply(session: AsyncSession, actor: MemorySubject, orders: list[SynthOrder], *, turn_on_history: bool = False) -> dict[str, int]:
    await _require_history(session, actor, turn_on_history)
    removed = await remove_synthetic(session, actor, keep={order.order_id for order in orders})
    graph = PurchaseGraphService(session)
    imported = 0
    for provider, order_list in order_lists(orders).items():
        result = await graph.import_orders(actor, order_list, provider=provider)
        imported += result.imported_orders
    return {"new": imported, "kept_or_updated": len(orders) - imported, "removed_stale": removed}


async def count_synthetic(session: AsyncSession, actor: MemorySubject | None) -> int:
    if actor is None:
        return 0
    rows = await session.execute(
        select(PurchaseOrderNode.id).where(
            PurchaseOrderNode.subject_id == actor.id,
            PurchaseOrderNode.provider_order_id.startswith(SYNTHETIC_ORDER_PREFIX),
        )
    )
    return len(rows.all())


def require_acknowledged(args: argparse.Namespace) -> None:
    if get_settings().commerce_mode == "live" and not args.i_understand_synthetic:
        raise SynthRefused(
            "COMMERCE_MODE is live: this database holds real data. Pass --i-understand-synthetic "
            "to add (or remove) clearly marked synthetic demo orders anyway."
        )


async def run(args: argparse.Namespace, session: AsyncSession) -> list[str]:
    require_acknowledged(args)
    external_id, actor = await resolve_user(session, args.user_id)
    out = [f"user={mask(external_id)}"]
    if args.remove:
        if actor is None:
            return [*out, "No synthetic orders for this user."]
        if not args.apply_remove:
            return [*out, f"Would remove {await count_synthetic(session, actor)} synthetic orders (pass --apply with --remove)."]
        return [*out, f"Removed {await remove_synthetic(session, actor)} synthetic orders and products only they used."]
    if not args.file:
        raise SynthRefused("Pass --file <answers.yaml> (or --remove).")
    answers = load_file(Path(args.file))
    end = date.fromisoformat(args.end) if args.end else None
    orders = generate(answers, months=args.months, seed=args.seed, end=end, realistic=args.realistic)
    if not args.apply:
        existing = await count_synthetic(session, actor)
        return [*out, f"Dry run: nothing written. Synthetic orders there now: {existing}.", *report(orders)]
    if actor is None:
        actor = await MemoryService(session).actor_for_external_id(external_id)
    counts = await apply(session, actor, orders, turn_on_history=args.turn_on_history)
    return [
        *out,
        f"Applied: {counts['new']} new, {counts['kept_or_updated']} already there (updated), "
        f"{counts['removed_stale']} stale removed.",
        *report(orders),
    ]


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(
        prog="make memory-synth ARGS=...",
        description="Synthetic demo order history consistent with the household answers (dry run by default).",
    )
    cli.add_argument("--file", help="The household answers file (same format as make memory-seed).")
    cli.add_argument("--months", type=int, default=12)
    cli.add_argument("--seed", type=int, default=42)
    cli.add_argument(
        "--realistic",
        action="store_true",
        help="Group purchases into 3-4 orders a week instead of one order per item and time of day.",
    )
    cli.add_argument("--end", help="Last day of history, YYYY-MM-DD (default yesterday, IST).")
    cli.add_argument("--user-id", help="Clerk user id; defaults to the only signed-in user.")
    mode = cli.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Print the plan only (default).")
    mode.add_argument("--apply", action="store_true", help="Write (or, with --remove, delete).")
    cli.add_argument("--remove", action="store_true", help="Delete every synthetic order of this user (with --apply).")
    cli.add_argument("--turn-on-history", action="store_true", help="Turn purchase history on for this user if off.")
    cli.add_argument("--i-understand-synthetic", action="store_true", help="Required when COMMERCE_MODE=live.")
    return cli


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    args = parser().parse_args(argv)
    # "--remove --apply" deletes; "--remove" alone only counts.
    args.apply_remove = bool(args.remove and args.apply)
    if args.remove:
        args.apply = False
    return args


async def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    from app.database.session import get_session_factory

    try:
        async with get_session_factory()() as session:
            for line in await run(args, session):
                print(line)
    except SeedError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    asyncio.run(main())
