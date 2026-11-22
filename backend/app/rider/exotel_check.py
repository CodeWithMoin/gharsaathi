"""Read-only Exotel account check: lists the account's ExoPhones, masked.

    make exotel-check        (or: scripts/backend-cli.sh app.rider.exotel_check)

One GET to the documented ExoPhones API
(https://developer.exotel.com/docs/exophones/api-reference/list-numbers):
``GET https://<subdomain>/v2_beta/Accounts/<sid>/IncomingPhoneNumbers`` with HTTP
Basic auth. It never places a call, buys a number or changes a flow, and prints
no credentials and no full phone number.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.rider.protocol import mask_number

ALLOWED_SUBDOMAINS = {"api.exotel.com", "api.in.exotel.com"}
REQUIRED = ("EXOTEL_ACCOUNT_SID", "EXOTEL_API_KEY", "EXOTEL_API_TOKEN", "EXOTEL_SUBDOMAIN")


def missing_variables(settings: Settings) -> list[str]:
    values = {
        "EXOTEL_ACCOUNT_SID": settings.exotel_account_sid.strip(),
        "EXOTEL_API_KEY": settings.exotel_api_key.strip(),
        "EXOTEL_API_TOKEN": settings.exotel_api_token.get_secret_value().strip(),
        "EXOTEL_SUBDOMAIN": settings.exotel_subdomain.strip(),
    }
    return [name for name in REQUIRED if not values[name]]


def numbers_url(settings: Settings) -> str:
    subdomain = settings.exotel_subdomain.strip().removeprefix("https://").removeprefix("@").rstrip("/")
    if subdomain not in ALLOWED_SUBDOMAINS:
        raise ValueError("EXOTEL_SUBDOMAIN must be api.exotel.com or api.in.exotel.com.")
    sid = settings.exotel_account_sid.strip()
    if not sid or "/" in sid or "?" in sid:
        raise ValueError("EXOTEL_ACCOUNT_SID looks wrong.")
    return f"https://{subdomain}/v2_beta/Accounts/{sid}/IncomingPhoneNumbers"


def describe(payload: Any) -> list[str]:
    numbers = payload.get("incoming_phone_numbers") if isinstance(payload, dict) else None
    if not isinstance(numbers, list):
        raise ValueError("Unexpected response shape (no incoming_phone_numbers list).")
    lines = []
    for item in numbers:
        if not isinstance(item, dict):
            continue
        phone = item.get("phone_number") if isinstance(item.get("phone_number"), str) else ""
        capabilities = item.get("capabilities") if isinstance(item.get("capabilities"), dict) else {}
        voice = "voice" if capabilities.get("voice") else "no voice"
        lines.append(f"  {mask_number(phone)} ({voice})")
    return lines


async def check(settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> int:
    missing = missing_variables(settings)
    if missing:
        print("Exotel check skipped. Add these to .env (values from the Exotel dashboard → API settings):")
        for name in missing:
            print(f"  {name}=")
        return 2
    try:
        url = numbers_url(settings)
    except ValueError as exc:
        print(f"Exotel check: {exc}")
        return 2
    auth = (settings.exotel_api_key.strip(), settings.exotel_api_token.get_secret_value().strip())
    try:
        async with httpx.AsyncClient(timeout=15.0, transport=transport) as client:
            response = await client.get(url, auth=auth)
    except httpx.HTTPError as exc:
        print(f"Exotel check: could not reach Exotel ({type(exc).__name__}).")
        return 1
    if response.status_code in {401, 403}:
        print(f"Exotel check: Exotel refused the API key/token (HTTP {response.status_code}).")
        return 1
    if response.status_code >= 400:
        print(f"Exotel check: Exotel answered HTTP {response.status_code}.")
        return 1
    try:
        lines = describe(response.json())
    except ValueError as exc:
        print(f"Exotel check: {exc}")
        return 1
    print(f"ExoPhones on this account: {len(lines)}")
    for line in lines:
        print(line)
    if not lines:
        print("No ExoPhone yet: get one in the Exotel dashboard before setting up call forwarding.")
    return 0


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv)
    return asyncio.run(check(get_settings()))


if __name__ == "__main__":
    sys.exit(main())
