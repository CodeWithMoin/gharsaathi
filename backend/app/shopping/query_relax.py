"""Deterministic broader search text for a query that matched nothing.

Models often search a product's full catalogue title ("Paper Boat Swing Coconut Water Drink 270").
Stores match on shorter text, so an empty answer gets one retry with the size and descriptor
words dropped. Only used after an empty result, never to replace the first query.
"""

from __future__ import annotations

import re

_SIZE = re.compile(r"^\d+([.,]\d+)?\s*(ml|l|ltr|litre|liter|g|gm|gram|kg|pc|pcs|pack|x)?$", re.IGNORECASE)
_NOISE = {"drink", "pack", "combo", "of", "the", "and", "with", "|", "-", "–", "—", "ml", "l", "ltr", "litre", "liter", "g", "gm", "gram", "kg", "pc", "pcs", "x"}
MAX_WORDS = 3


def relaxed_query(query: str) -> str | None:
    """A shorter query (brand + product words), or None when nothing broader exists."""

    text = re.sub(r"\([^)]*\)", " ", query)
    words = [word for word in re.split(r"[\s|,]+", text) if word]
    kept = [word for word in words if not _SIZE.match(word) and word.lower() not in _NOISE]
    relaxed = " ".join(kept[:MAX_WORDS]).strip()
    if not relaxed or relaxed.casefold() == " ".join(query.split()).casefold():
        return None
    return relaxed
