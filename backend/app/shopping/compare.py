"""Deterministic comparison of products already shown to the user (``compare_options``).

Pure functions only: no provider calls and no model maths. Every amount is
integer paise and every string the model or the screen shows is formatted by
``app.shopping.money.format_rupees``.

Unit prices
-----------
A pack size is read from the provider's ``pack_size`` (else the product name):
``g``/``kg`` become grams, ``ml``/``L`` millilitres and ``pc``/``pcs``/``piece``
pieces; ``6 x 330 ml``, ``330 ml x 6``, ``pack of 6`` and ``6 pack`` multiply the
pack. Grams and millilitres are priced per 100, pieces per piece. Grams and
millilitres are never compared with each other (no density is assumed), and a
size that cannot be read gives "unit price not comparable".

Rounding: the unit price is ``price_paise * base / quantity`` rounded half-up to
a whole paisa. Differences, "same value" and the best value are worked out on
those rounded paise, so every string matches the unit prices the user sees
(₹10 vs ₹6.50 per 100 ml is "₹3.50 cheaper per 100 ml").
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from itertools import combinations
from typing import Any, Literal

from app.memory.remembered import names_match
from app.shopping.money import format_rupees

UnitKind = Literal["mass", "volume", "count"]

NOT_COMPARABLE = "unit price not comparable"
UNIT_LABELS: dict[UnitKind, str] = {"mass": "per 100 g", "volume": "per 100 ml", "count": "per piece"}
_BASE: dict[UnitKind, int] = {"mass": 100, "volume": 100, "count": 1}

_UNIT = (
    r"kgs?|kilograms?|kilos?|grams?|gms?|gm|g"
    r"|millilit(?:er|re)s?|ml|lit(?:er|re)s?|ltrs?|ltr|l"
    r"|pieces?|pcs?|units?|nos"
)
_AMOUNT = re.compile(rf"(\d+(?:\.\d+)?)\s*({_UNIT})\b")
_COUNT_BEFORE = re.compile(r"(\d+)\s*x\s*$")
_COUNT_AFTER = re.compile(r"^\s*x\s*(\d+)\b")
_PACK_OF = re.compile(r"\bpack\s+of\s+(\d+)\b|\b(\d+)\s*-?\s*(?:pack|pk)\b")


@dataclass(frozen=True)
class PackSize:
    """A whole pack: ``quantity`` grams, millilitres or pieces in ``count`` units."""

    kind: UnitKind
    quantity: Decimal
    count: int = 1


def _kind(unit: str) -> tuple[UnitKind, Decimal]:
    if unit.startswith(("kg", "kilo")):
        return "mass", Decimal(1000)
    if unit.startswith(("g", "gm")):
        return "mass", Decimal(1)
    if unit.startswith(("ml", "milli")):
        return "volume", Decimal(1)
    if unit.startswith("l"):
        return "volume", Decimal(1000)
    return "count", Decimal(1)


def parse_pack(text: str | None) -> PackSize | None:
    """``"6 x 330 ml"`` -> volume 1980 ml in 6 units; ``"1 kg"`` -> mass 1000 g; else None."""

    if not text:
        return None
    folded = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text.casefold().replace("×", "x").replace("*", "x"))
    match = _AMOUNT.search(folded)
    if match is None:
        return None
    amount = Decimal(match.group(1))
    kind, factor = _kind(match.group(2))
    count = 1
    before = _COUNT_BEFORE.search(folded[: match.start()])
    after = _COUNT_AFTER.search(folded[match.end():])
    if before:
        count = int(before.group(1))
    elif after:
        count = int(after.group(1))
    elif kind != "count" and (pack_of := _PACK_OF.search(folded)):
        count = int(pack_of.group(1) or pack_of.group(2))
    quantity = amount * factor * count
    if quantity <= 0 or count <= 0:
        return None
    return PackSize(kind, quantity, count)


def pack_of(pack_size: str | None, name: str = "") -> PackSize | None:
    """The provider's pack size when it can be read, else a size written in the name."""

    return parse_pack(pack_size) or parse_pack(name)


def unit_price_paise(price_paise: int, pack: PackSize) -> int:
    """Paise per 100 g / 100 ml / piece, rounded half-up to a whole paisa."""

    exact = Decimal(price_paise) * _BASE[pack.kind] / pack.quantity
    return int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def unit_price_text(paise: int, kind: UnitKind) -> str:
    return f"{format_rupees(paise)} {UNIT_LABELS[kind]}"


def price_difference(left_paise: int, right_paise: int) -> str:
    """``"₹12 cheaper"`` (the amount the cheaper one saves) or ``"same price"``."""

    difference = abs(left_paise - right_paise)
    return "same price" if difference == 0 else f"{format_rupees(difference)} cheaper"


def unit_price_difference(
    left: tuple[int, UnitKind] | None, right: tuple[int, UnitKind] | None
) -> str:
    """``"₹1.50 cheaper per 100 ml"``, ``"same per 100 ml"`` or "unit price not comparable"."""

    if left is None or right is None or left[1] != right[1]:
        return NOT_COMPARABLE
    difference = abs(left[0] - right[0])
    label = UNIT_LABELS[left[1]]
    return f"same {label}" if difference == 0 else f"{format_rupees(difference)} cheaper {label}"


@dataclass(frozen=True)
class CompareItem:
    """One shown option, in the order the user named them (``ref`` 1..N)."""

    ref: int
    name: str
    store: str
    store_id: str
    price_paise: int
    available: bool
    pack_size: str | None = None
    mrp_paise: int | None = None
    delivery_eta: str | None = None
    position: int | None = None
    item_label: str | None = None
    option_id: str | None = None

    @property
    def pack(self) -> PackSize | None:
        return pack_of(self.pack_size, self.name)

    @property
    def unit(self) -> tuple[int, UnitKind] | None:
        pack = self.pack
        return None if pack is None else (unit_price_paise(self.price_paise, pack), pack.kind)


def same_product_different_store(left: CompareItem, right: CompareItem) -> bool:
    """The same product and pack size, offered by two different stores."""

    if left.store_id == right.store_id or not names_match(left.name, right.name):
        return False
    return left.pack is not None and left.pack == right.pack


def _labels(items: list[CompareItem]) -> dict[int, str]:
    """How the strings name each option: the store when the names match, else the name."""

    if all(names_match(items[0].name, item.name) for item in items[1:]) and len({i.store_id for i in items}) == len(items):
        return {item.ref: item.store for item in items}
    labels: dict[int, str] = {}
    for item in items:
        twins = [other for other in items if other is not item and other.name.casefold() == item.name.casefold()]
        label = item.name
        if any(other.pack != item.pack for other in twins) and item.pack_size:
            label = f"{label} {item.pack_size}"
        if any(other.pack == item.pack for other in twins):
            label = f"{label} ({item.store})"
        labels[item.ref] = label
    return labels


def _lowest(values: dict[int, int]) -> int | None:
    """The one ref with the lowest value; None on a tie for lowest or fewer than two."""

    if len(values) < 2:
        return None
    best = min(values.values())
    winners = [ref for ref, value in values.items() if value == best]
    return winners[0] if len(winners) == 1 else None


def compare(items: list[CompareItem]) -> dict[str, Any]:
    """Per-option facts, pairwise differences, the cheapest and the best value (in stock only)."""

    labels = _labels(items)
    options: list[dict[str, Any]] = []
    for item in items:
        entry: dict[str, Any] = {
            "ref": item.ref,
            "label": labels[item.ref],
            "name": item.name,
            "store": item.store,
            "store_id": item.store_id,
            "price_paise": item.price_paise,
            "price": format_rupees(item.price_paise),
            "available": item.available,
        }
        if item.option_id:
            entry["option_id"] = item.option_id
        if item.position is not None:
            entry["position"] = item.position
        if item.item_label:
            entry["item"] = item.item_label
        if item.pack_size:
            entry["pack_size"] = item.pack_size
        if item.mrp_paise is not None and item.mrp_paise > item.price_paise:
            entry["mrp"] = format_rupees(item.mrp_paise)
            entry["saving"] = format_rupees(item.mrp_paise - item.price_paise)
        if item.delivery_eta:
            entry["delivery_eta"] = item.delivery_eta
        unit = item.unit
        entry["unit_price"] = unit_price_text(*unit) if unit else NOT_COMPARABLE
        options.append(entry)

    pairs: list[dict[str, Any]] = []
    for left, right in combinations(items, 2):
        left_unit, right_unit = left.unit, right.unit
        pair: dict[str, Any] = {
            "options": [left.ref, right.ref],
            "price_difference": price_difference(left.price_paise, right.price_paise),
            "unit_price_difference": unit_price_difference(left_unit, right_unit),
            "same_product_different_store": same_product_different_store(left, right),
        }
        if left.price_paise != right.price_paise:
            cheaper = left if left.price_paise < right.price_paise else right
            pair["cheaper"] = cheaper.ref
            pair["price_line"] = f"{labels[cheaper.ref]} is {pair['price_difference']}"
        else:
            pair["price_line"] = "same price"
        if left_unit and right_unit and left_unit[1] == right_unit[1] and left_unit[0] != right_unit[0]:
            better = left if left_unit[0] < right_unit[0] else right
            pair["better_unit_price"] = better.ref
            pair["unit_price_line"] = f"{labels[better.ref]} is {pair['unit_price_difference']}"
        else:
            pair["unit_price_line"] = pair["unit_price_difference"]
        if not (left.available and right.available):
            pair["out_of_stock"] = [item.ref for item in (left, right) if not item.available]
        pairs.append(pair)

    in_stock = [item for item in items if item.available]
    result: dict[str, Any] = {
        "options": options,
        "pairs": pairs,
        "same_product_different_store": any(pair["same_product_different_store"] for pair in pairs),
    }
    cheapest = _lowest({item.ref: item.price_paise for item in in_stock})
    if cheapest is not None:
        result["cheapest"] = {"ref": cheapest, "label": labels[cheapest], "price": format_rupees(next(i for i in items if i.ref == cheapest).price_paise)}
    elif len(in_stock) >= 2 and len({item.price_paise for item in in_stock}) == 1:
        result["all_in_stock_same_price"] = True

    units = {item.ref: item.unit for item in in_stock}
    kinds = {unit[1] for unit in units.values() if unit is not None}
    if len(in_stock) >= 2 and len(kinds) == 1 and all(unit is not None for unit in units.values()):
        kind = next(iter(kinds))
        best = _lowest({ref: unit[0] for ref, unit in units.items() if unit is not None})
        if best is not None:
            best_unit = units[best]
            assert best_unit is not None
            result["better_value"] = {
                "ref": best,
                "label": labels[best],
                "unit_price": unit_price_text(*best_unit),
                "badge": f"Best value {UNIT_LABELS[kind]}",
            }
        else:
            result["better_value"] = None
            result["value_note"] = f"same {UNIT_LABELS[kind]}"
    else:
        result["better_value"] = None
        result["value_note"] = NOT_COMPARABLE if len(in_stock) >= 2 else "fewer than two options in stock"
    return result
