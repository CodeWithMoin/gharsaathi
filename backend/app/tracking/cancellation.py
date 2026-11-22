"""Detect whether a live provider catalogue exposes order cancellation.

GharSaathi does not cancel orders. Zepto's documented MCP catalogue (snapshot
``tests/fixtures/zepto_tools_catalogue_2026-11-21.json``) has no cancel tool,
so the household is told to cancel in the Zepto app. If a later catalogue adds
one, status results report ``cancel_supported: true`` so the gap is visible;
calling such a tool would need its own tap-confirmed card, like Place order.
Nothing in this module, or anywhere else, calls it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

_WORDS = re.compile(r"[a-z]+")
_ORDER_ID_KEYS = {"orderid", "order_id", "ordercode", "order_code"}


def _words(name: str) -> set[str]:
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
    return set(_WORDS.findall(spaced.casefold()))


def _schema_names_an_order(schema: object) -> bool:
    if not isinstance(schema, dict):
        return False
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return False
    return any(str(key).casefold() in _ORDER_ID_KEYS for key in properties)


def is_order_cancel_tool(name: str, input_schema: object = None) -> bool:
    """A tool clearly meant to cancel an order: "cancel" plus "order" in its
    name, or a bare "cancel" tool whose input schema takes an order id.
    "cancel_payment" and similar are not order cancellation."""

    words = _words(name)
    cancel_words = {"cancel", "cancellation"} & words
    if not cancel_words:
        return False
    if "order" in words or "orders" in words:
        return True
    return words == cancel_words and _schema_names_an_order(input_schema)


def order_cancel_tool(tools: Iterable[Any]) -> str | None:
    """The name of a discovered order-cancellation tool, or None."""

    for tool in tools:
        name = getattr(tool, "name", None)
        if isinstance(name, str) and is_order_cancel_tool(
            name, getattr(tool, "input_schema", None)
        ):
            return name
    return None
