"""Content-free diagnostics for Zepto checkout responses.

A preview response contains the user's address, product names and prices, so
it must never be logged.  When a preview cannot be verified we log only its
*shape*: key paths, JSON types, list lengths and a few derived facts (counts,
comparisons, field names).  One failed attempt is then enough to see which
part of the live response differs from the parser's expectations.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any


logger = logging.getLogger(__name__)

PREVIEW_FAILURE_EVENT = "zepto_cod_preview_invalid"
MAX_SHAPE_ENTRIES = 200
MAX_SHAPE_DEPTH = 8
# Provider keys are schema names (camelCase identifiers).  Anything else, for
# example a dynamic key derived from an address or product, is masked.
_SAFE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,47}$")
_NUMERIC_TEXT = re.compile(r"^\s*-?\d+(\.\d+)?\s*$")


def json_type(value: object) -> str:
    """A coarse, value-free type label for one JSON value."""

    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int" if value >= 0 else "int(negative)"
    if isinstance(value, float):
        return "float(integral)" if value.is_integer() else "float"
    if isinstance(value, str):
        if not value.strip():
            return "str(empty)"
        if _NUMERIC_TEXT.match(value):
            return "str(numeric)"
        return "str"
    if isinstance(value, list):
        return f"array[{len(value)}]"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def payload_shape(value: object) -> dict[str, str]:
    """Flatten ``value`` into ``{key path: type}`` without any values.

    Array elements share the ``[]`` path; differing element types are joined
    with ``|`` so the diagnostic stays small for long lists.
    """

    shape: dict[str, set[str]] = {}
    truncated = False

    def visit(node: object, path: str, depth: int) -> None:
        nonlocal truncated
        if len(shape) >= MAX_SHAPE_ENTRIES and path not in shape:
            truncated = True
            return
        shape.setdefault(path, set()).add(json_type(node))
        if depth >= MAX_SHAPE_DEPTH:
            return
        if isinstance(node, dict):
            for key in sorted(node, key=str):
                name = key if isinstance(key, str) and _SAFE_KEY.match(key) else "<key>"
                visit(node[key], f"{path}.{name}" if path else name, depth + 1)
        elif isinstance(node, list):
            for item in node:
                visit(item, f"{path}[]", depth + 1)

    visit(value, "", 0)
    flattened = {path or "$": "|".join(sorted(types)) for path, types in shape.items()}
    if truncated:
        flattened["$truncated"] = "true"
    return flattened


def log_preview_failure(
    *,
    correlation_id: str,
    stage: str,
    step: str,
    outcome: str,
    payload: object | None,
    facts: dict[str, Any] | None = None,
) -> None:
    """Log one structured, content-free record for a failed Zepto preview."""

    logger.warning(
        "event=%s correlation_id=%s stage=%s step=%s outcome=%s facts=%s shape=%s",
        PREVIEW_FAILURE_EVENT,
        correlation_id,
        stage,
        step,
        outcome,
        json.dumps(facts or {}, sort_keys=True, separators=(",", ":")),
        json.dumps(
            payload_shape(payload) if payload is not None else {},
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


CONFIRM_FAILURE_EVENT = "zepto_cod_confirm_unparsed"


def log_confirm_failure(
    *,
    correlation_id: str,
    step: str,
    outcome: str,
    payload: object | None,
    facts: dict[str, Any] | None = None,
) -> None:
    """Log one structured, content-free record for an unreadable order confirmation.

    The order may have been placed, so the attempt stays ``outcome_unknown``;
    this record only shows which part of Zepto's response the parser did not
    recognise (key paths and types, never values or text).
    """

    text = payload.get("content_text") if isinstance(payload, dict) else None
    derived = dict(facts or {})
    if isinstance(text, str):
        derived["text_chars"] = len(text)
    logger.warning(
        "event=%s correlation_id=%s stage=confirm step=%s outcome=%s facts=%s shape=%s",
        CONFIRM_FAILURE_EVENT,
        correlation_id,
        step,
        outcome,
        json.dumps(derived, sort_keys=True, separators=(",", ":")),
        json.dumps(
            payload_shape(payload) if payload is not None else {},
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
