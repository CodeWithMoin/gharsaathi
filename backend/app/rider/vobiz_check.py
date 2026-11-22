"""Read-only Vobiz account check: lists the account's phone numbers, masked.

    make vobiz-check        (or: scripts/backend-cli.sh app.rider.vobiz_check)

One GET to the documented Phone Numbers API
(https://www.vobiz.ai/docs/account-phone-number/list-account-phone-numbers):
``GET https://api.vobiz.ai/api/v1/Account/{auth_id}/numbers`` with the
``X-Auth-ID`` / ``X-Auth-Token`` headers. It never buys or releases a number,
places a call or changes an application, and prints no credentials and no full
phone number.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.rider.telephony import mask_number

API_BASE = "https://api.vobiz.ai/api/v1"
REQUIRED = ("VOBIZ_AUTH_ID", "VOBIZ_AUTH_TOKEN")
AUTH_ID = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
PER_PAGE = 50


def missing_variables(settings: Settings) -> list[str]:
    values = {
        "VOBIZ_AUTH_ID": settings.vobiz_auth_id.strip(),
        "VOBIZ_AUTH_TOKEN": settings.vobiz_auth_token.get_secret_value().strip(),
    }
    return [name for name in REQUIRED if not values[name]]


def numbers_url(settings: Settings) -> str:
    auth_id = settings.vobiz_auth_id.strip()
    if not AUTH_ID.match(auth_id):
        raise ValueError("VOBIZ_AUTH_ID looks wrong (letters, digits, _ and - only).")
    return f"{API_BASE}/Account/{auth_id}/numbers"


def describe(payload: Any) -> tuple[list[str], int | None]:
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ValueError("Unexpected response shape (no items list).")
    lines = []
    for item in items:
        if not isinstance(item, dict):
            continue
        number = item.get("e164") if isinstance(item.get("e164"), str) else ""
        voice = "voice" if item.get("voice_enabled") or (item.get("capabilities") or {}).get("voice") else "no voice"
        state = item.get("status") if isinstance(item.get("status"), str) else "unknown"
        app = "answer app set" if item.get("application_id") else "no answer app"
        lines.append(f"  {mask_number(number)} ({voice}, {state[:20]}, {app})")
    total = payload.get("total")
    return lines, total if isinstance(total, int) else None


async def check(settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> int:
    missing = missing_variables(settings)
    if missing:
        print("Vobiz check skipped. Add these to .env (Vobiz console → Settings → API):")
        for name in missing:
            print(f"  {name}=")
        return 2
    try:
        url = numbers_url(settings)
    except ValueError as exc:
        print(f"Vobiz check: {exc}")
        return 2
    headers = {
        "X-Auth-ID": settings.vobiz_auth_id.strip(),
        "X-Auth-Token": settings.vobiz_auth_token.get_secret_value().strip(),
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, transport=transport) as client:
            response = await client.get(url, headers=headers, params={"page": 1, "per_page": PER_PAGE})
    except httpx.HTTPError as exc:
        print(f"Vobiz check: could not reach Vobiz ({type(exc).__name__}).")
        return 1
    if response.status_code in {401, 403}:
        print(f"Vobiz check: Vobiz refused the Auth ID/token (HTTP {response.status_code}).")
        return 1
    if response.status_code >= 400:
        print(f"Vobiz check: Vobiz answered HTTP {response.status_code}.")
        return 1
    try:
        lines, total = describe(response.json())
    except ValueError as exc:
        print(f"Vobiz check: {exc}")
        return 1
    print(f"Vobiz numbers on this account: {total if total is not None else len(lines)}")
    for line in lines:
        print(line)
    if not lines:
        print("No number yet: buy one in the Vobiz console (India KYC) before setting up call forwarding.")
    return 0


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0]).parse_args(argv)
    return asyncio.run(check(get_settings()))


if __name__ == "__main__":
    sys.exit(main())
