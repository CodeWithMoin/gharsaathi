"""What the rider assistant must never say, and how its data is cleaned first.

The model is told the rules (prompts.py), but code enforces the data side:
every tool output and every stored summary passes ``redact_secrets``; order
status text from a store also loses any bare 4–8 digit code; delivery notes
may not contain OTPs, PINs, passwords or payment details at all.
"""

from __future__ import annotations

import re

HIDDEN = "[hidden]"

# A secret word followed (within a few characters) by a code.
_KEYWORD_CODE = re.compile(
    r"(?i)(\b(?:o\.?\s?t\.?\s?p|otp|pin|passcode|pass\s?code|password|verification\s+code|"
    r"security\s+code|delivery\s+code|code|cvv)\b|ओटीपी|पिन)"
    r"(\W{0,6}(?:is|hai|:|=|-)?\W{0,6})(\d[\d\s-]{2,10}\d)"
)
_BARE_CODE = re.compile(r"(?<![\d₹.,])\b\d{4,8}\b(?![\d.,])")
_SECRET_WORDS = re.compile(
    r"(?i)\b(?:otp|o\.t\.p|pin|passcode|password|cvv|upi|card\s+number)\b|ओटीपी|पिन"
)

# Rider requests the assistant must refuse, recorded for the owner's summary.
_OTP_ASK = re.compile(r"(?i)\b(?:otp|o\.?\s?t\.?\s?p|pin|code)\b|ओटीपी|ओ टी पी")
_MONEY_ASK = re.compile(
    r"(?i)\b(?:extra|charge|charges|tip|tips|online\s+pay\w*|pay\s+online|upi|qr|g\s?pay|phonepe|paytm|"
    r"refund|paise\s+(?:zyada|extra)|zyada\s+paise|fee|cash\s+back|scan)\b"
)
_ORDER_CHANGE_ASK = re.compile(
    r"(?i)\b(?:cancel|cancell?ation|address\s+(?:change|badal)|change\s+(?:the\s+)?address|return|replace)\w*\b"
)


def redact_secrets(text: str) -> str:
    """Hide any code that follows an OTP/PIN/password-like word."""

    return _KEYWORD_CODE.sub(lambda m: f"{m.group(1)}{m.group(2)}{HIDDEN}", text)


def redact_store_text(text: str) -> str:
    """Store-provided status text: secrets and any bare 4–8 digit code are hidden."""

    return _BARE_CODE.sub(HIDDEN, redact_secrets(text))


def mentions_secret(text: str) -> bool:
    return bool(_SECRET_WORDS.search(text))


def request_flags(text: str) -> set[str]:
    flags: set[str] = set()
    if _OTP_ASK.search(text):
        flags.add("otp")
    if _MONEY_ASK.search(text):
        flags.add("money")
    if _ORDER_CHANGE_ASK.search(text):
        flags.add("order_change")
    return flags


def clean_value(value: object, *, store_text: bool = False) -> object:
    """Recursively redact strings inside a tool output."""

    if isinstance(value, str):
        return redact_store_text(value) if store_text else redact_secrets(value)
    if isinstance(value, list):
        return [clean_value(item, store_text=store_text) for item in value]
    if isinstance(value, dict):
        return {key: clean_value(item, store_text=store_text) for key, item in value.items()}
    return value
